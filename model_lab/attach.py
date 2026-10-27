"""학습 서버가 만든 어댑터 두 개를 모델 패키지(ModelPackageManifest)로 만들어 model-registry에 게시한다.

파이프라인(train -> evaluate -> package -> publish)을 다시 돌리지 않고, 이미 학습된 어댑터 디렉터리만 받아
패키지로 만드는 길이다("AI만 얹기"). 입력: 어댑터 디렉터리 두 개(`adapter_model.safetensors` + `adapter_config.json`),
시스템 프롬프트 파일 두 개, 설정 파일(서빙 설정·패키지 ID·게시 대상), 프로파일 이름(기반 모델).

사실 그대로만 적는다: 이 길은 평가를 하지 않는다. 게시하는 EvaluationReport는 "평가하지 않았다"(`n=0`, 노트)로 표시한다.
어댑터 디렉터리에는 필요한 두 파일과 시스템 프롬프트만 복사하므로(그 밖의 파일은 해시에 들어가지 않는다) 같은 입력은
같은 패키지가 된다(멱등)."""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from boundary_contracts import PIPELINE_SCHEMA_VERSION, canonical, validator

from . import packager, schemas
from .config import ROLES, _read, deep_merge, load_config, resolved_path
from .errors import ConfigError, PipelineError

ADAPTER_FILES = ("adapter_model.safetensors", "adapter_config.json")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"


def load_profile(name: str) -> dict:
    """profiles/<name>.yaml. 이름은 파일 이름뿐이다(경로 불가)."""
    if not _NAME.match(name):
        raise ConfigError("프로파일 이름은 파일 이름이어야 한다(경로 불가)")
    p = PROFILES_DIR / f"{name}.yaml"
    if not p.is_file():
        raise ConfigError(f"프로파일이 없다: {name} (profiles/ 아래: {', '.join(sorted(x.stem for x in PROFILES_DIR.glob('*.yaml')))})")
    return _read(p)


def check_adapter(role: str, adapter_dir: Path, base_model: dict, max_rank: int | None) -> dict:
    """어댑터 디렉터리의 필수 파일과 기반 모델·순위를 확인한다. adapter_config의 내용을 돌려준다."""
    adapter_dir = Path(adapter_dir)
    for f in ADAPTER_FILES:
        if not (adapter_dir / f).is_file():
            raise PipelineError(f"{role}: 어댑터 디렉터리에 {f}가 없다")
    try:
        conf = json.loads((adapter_dir / "adapter_config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PipelineError(f"{role}: adapter_config.json을 읽을 수 없다") from None
    if not isinstance(conf, dict):
        raise PipelineError(f"{role}: adapter_config.json의 최상위는 객체여야 한다")
    got = conf.get("base_model_name_or_path")
    if got != base_model["id"]:
        raise PipelineError(f"{role}: 어댑터의 base_model_name_or_path({got!r})가 프로파일의 기반 모델({base_model['id']!r})과 다르다. "
                            "다른 기반 모델로 학습된 어댑터는 얹을 수 없다")
    r = conf.get("r")
    if max_rank is not None and (not isinstance(r, int) or r > max_rank):
        raise PipelineError(f"{role}: 어댑터 순위 {r}가 대상 엔진의 max_lora_rank {max_rank}를 넘는다")
    return conf


def _newest_mtime(*paths: Path) -> str:
    t = max(Path(p).stat().st_mtime for p in paths)
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _not_evaluated_report(*, role, package, dataset, created_at, decoding, report_id) -> dict:
    r = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "report_id": report_id, "created_at": created_at, "role": role,
         "package": package, "dataset": dataset, "question_sets": [],
         "evaluation_backend": {"name": "none", "version": "0", "constrained_decoding": False},
         "decoding": {k: decoding[k] for k in ("temperature", "seed") if k in decoding},
         "metrics": [{"name": "evaluated", "group": "OTHER", "value": 0, "n": 0, "higher_is_better": True, "unit": "count"}],
         "notes": ["평가하지 않았다: 학습 서버에서 만든 어댑터를 model-lab package-adapters로 패키징만 했다(n=0은 계산하지 못했다는 뜻)."],
         "ext": {"model-lab": {"attached": True}}}
    r["content_hash"] = canonical.content_hash(r)
    validator("EvaluationReport").validate(r)
    return r


def package_adapters(*, config: str | Path, adapters: dict, prompts: dict, out: Path, profile: str | None = None,
                     dataset: dict, training_run_id: str | None = None, version: str | None = None,
                     base_revision: str | None = None, created_at: str | None = None, registry=None, publish: bool = True) -> dict:
    """adapters, prompts: {"LORA_A": 경로, "LORA_B": 경로}. dataset: {id, version, content_hash} (어댑터를 학습한 데이터셋).
    out: 패키지(매니페스트·평가 보고서·어댑터)를 쓸 디렉터리. publish=True면 설정의 registry(또는 인자)에 게시한다."""
    cfg = load_config(config)
    if profile:
        cfg = deep_merge(cfg, {k: v for k, v in load_profile(profile).items() if k in ("base_model", "checks")})
    if "base_model" not in cfg:
        raise ConfigError("기반 모델이 없다: 프로파일(--profile 또는 설정의 profile:)이 필요하다")
    for r in ROLES:
        if r not in cfg.get("serving", {}) or r not in cfg["roles"]:
            raise ConfigError(f"설정에 serving.{r}와 roles.{r}가 필요하다")
    _check_ref(dataset)
    max_rank = cfg.get("checks", {}).get("max_lora_rank")
    base = {"id": cfg["base_model"]["id"], "revision": base_revision or cfg["base_model"].get("revision") or "unspecified"}
    confs = {r: check_adapter(r, Path(adapters[r]), base, max_rank) for r in ROLES}
    texts = {}
    for r in ROLES:
        try:
            texts[r] = Path(prompts[r]).read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            raise PipelineError(f"{r}: 시스템 프롬프트 파일을 읽을 수 없다") from None
        if not texts[r].strip():
            raise PipelineError(f"{r}: 시스템 프롬프트 파일이 비어 있다")
    created = created_at or _newest_mtime(*[Path(adapters[r]) / f for r in ROLES for f in ADAPTER_FILES], *[prompts[r] for r in ROLES])
    contract_version = schemas.contract_version(resolved_path(cfg, cfg["inputs"]["schema_dir"]))
    out = Path(out)
    result = {}
    staged = {}
    for r in ROLES:   # 어댑터 해시 -> 버전. 두 어댑터 중 하나만 바뀌어도 그 역할의 패키지가 새로 생긴다
        ad = out / "adapters" / r
        if ad.exists():
            shutil.rmtree(ad)
        ad.mkdir(parents=True)
        for f in ADAPTER_FILES:
            shutil.copyfile(Path(adapters[r]) / f, ad / f)
        sp = packager.install_system_prompt(ad, texts[r])
        staged[r] = (ad, sp)
    for r in ROLES:
        ad, sp = staged[r]
        ahash = canonical.directory_hash(ad)
        ver = version or packager.safe_id("attach-" + ahash.split(":")[-1][:12])
        run_id = training_run_id or packager.safe_id("attach-" + ahash.split(":")[-1][:12])
        pid = cfg["roles"][r]["package_id"]
        m = packager.build_manifest(
            role=r, system_prompt=sp, package_id=pid, version=ver, created_at=created, contract_version=contract_version,
            base_model=base, adapter_dir=ad, serving_config=cfg["serving"][r], dataset_ref=dataset, dataset_manifest={"files": []},
            run_id=run_id, ext={"attached": True, "trainer": "external", "adapter_config_r": confs[r].get("r"),
                                "evaluated": False})
        rep = _not_evaluated_report(role=r, package={"id": pid, "version": ver, "content_hash": m["content_hash"]},
                                    dataset=dataset, created_at=created, decoding=cfg["serving"][r]["decoding"], report_id=m["eval_report_ref"])
        rb = canonical.canonicalize(rep)
        (out / "package").mkdir(parents=True, exist_ok=True)
        (out / "package" / f"{r}.manifest.json").write_bytes(canonical.canonicalize(m))
        (out / "package" / f"{r}.evaluation-report.json").write_bytes(rb)
        result[r] = {"package_id": pid, "version": ver, "content_hash": m["content_hash"], "adapter_hash": m["adapter_hash"], "published": False}
        if publish:
            reg = registry
            if reg is None:
                from .pipeline import make_registry
                reg = make_registry(cfg)
            got = reg.publish(m, ad, rb)
            result[r].update(published=True, content_hash=got["content_hash"])
    return result


def _check_ref(ref: dict) -> None:
    if not isinstance(ref, dict) or set(ref) != {"id", "version", "content_hash"} or not all(isinstance(v, str) and v for v in ref.values()):
        raise ConfigError("dataset은 {id, version, content_hash}여야 한다")
