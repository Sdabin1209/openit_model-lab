"""학습 서버가 만든 어댑터 두 개를 모델 패키지(ModelPackageManifest)로 만들어 model-registry에 게시한다.

파이프라인(train -> evaluate -> package -> publish)을 다시 돌리지 않고, 이미 학습된 어댑터 디렉터리만 받아
패키지로 만드는 길이다("AI만 얹기"). 입력: 어댑터 디렉터리 두 개(`adapter_model.safetensors` + `adapter_config.json`),
시스템 프롬프트 파일 두 개, 설정 파일(서빙 설정·패키지 ID·게시 대상), 프로파일 이름(기반 모델).

사실 그대로만 적는다: 이 길은 평가를 하지 않는다. 게시하는 EvaluationReport는 "평가하지 않았다"(`n=0`, 노트)로 표시한다.
어댑터 디렉터리에는 필요한 두 파일과 시스템 프롬프트만 복사하므로(그 밖의 파일은 해시에 들어가지 않는다) 같은 입력은
같은 패키지가 된다(멱등)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from boundary_contracts import canonical

from ..components import adapter_check, evaluation_report_build, package_build as packager, train_record_verify
from ..components.adapter_check import ADAPTER_FILES
from ..config import load_config, load_profile
from . import schemas
from .errors import ConfigError, PipelineError
from .settings import ROLES, contract_version as _contract_version, deep_merge, resolved_path


# 왜: created_at이 content_hash에 들어가므로 파일 시스템 mtime(복사 방식에 따라 달라짐)에서 읽으면 같은 내용이 다른 패키지가 되어
# 재게시가 CONFLICT가 된다(점검 T22). 시각은 입력에서만 온다: --created-at > SOURCE_DATE_EPOCH(reproducible-builds.org 규격: UTC 에포크 초의
# 10진 정수) > 없으면 거부. 학습 시각을 이 영역은 알 수 없으므로 지어내지 않는다(ADR 0014, 0020).
def resolve_created_at(created_at: str | None, environ=os.environ) -> str:
    if created_at:
        return created_at
    raw = environ.get("SOURCE_DATE_EPOCH")
    if raw is None:
        raise ConfigError("created_at을 정할 수 없다. `--created-at 2026-10-29T00:00:00Z`로 주거나 환경 변수 "
                          "`SOURCE_DATE_EPOCH`(UTC 에포크 초, 예: 1793232000)를 설정한다. 시각을 지어내지 않는다")
    if not re.fullmatch(r"[0-9]+", raw.strip()):
        raise ConfigError("SOURCE_DATE_EPOCH는 UTC 에포크 초의 10진 정수여야 한다(reproducible-builds.org 규격). "
                          "무시하지 않고 거부한다. `--created-at`으로 직접 줄 수도 있다")
    try:
        return datetime.fromtimestamp(int(raw), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        raise ConfigError("SOURCE_DATE_EPOCH가 시각 범위를 벗어났다") from None


def package_adapters(*, config: str | Path, adapters: dict, prompts: dict, out: Path, profile: str | None = None,
                     dataset: dict, training_run_id: str | None = None, version: str | None = None,
                     base_revision: str | None = None, created_at: str | None = None, registry=None, publish: bool = True,
                     train_records: dict | None = None) -> dict:
    """adapters, prompts: {"LORA_A": 경로, "LORA_B": 경로}. dataset: {id, version, content_hash} (어댑터를 학습한 데이터셋).
    train_records: {"LORA_A": 경로, ...} `train`이 쓴 학습 기록(선택). 있으면 어댑터·데이터셋·기반 모델과 대조해 계보(`ext."model-lab".lineage`)로
    패키지에 넣는다. 없는 역할은 계보가 비어 있다는 경고를 결과의 `warnings`에 남긴다(ML-5; 계약의 정식 필드 승격은 변경 요청 몫).
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
    created = resolve_created_at(created_at)      # 아무것도 만들기 전에 정한다
    max_rank = cfg.get("checks", {}).get("max_lora_rank")
    base = {"id": cfg["base_model"]["id"], "revision": base_revision or cfg["base_model"].get("revision") or "unspecified"}
    confs = {r: adapter_check.check_adapter(r, Path(adapters[r]), base, max_rank) for r in ROLES}
    texts = {}
    for r in ROLES:
        try:
            texts[r] = Path(prompts[r]).read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            raise PipelineError(f"{r}: 시스템 프롬프트 파일을 읽을 수 없다") from None
        if not texts[r].strip():
            raise PipelineError(f"{r}: 시스템 프롬프트 파일이 비어 있다")
    lineage, warnings = {}, {}
    for r in ROLES:
        rp = (train_records or {}).get(r)
        if rp:
            lineage[r] = train_record_verify.read_train_record(r, rp, adapter_dir=Path(adapters[r]), base=base, dataset=dataset)
        else:
            lineage[r] = {"train_record_sha256": None}
            warnings[r] = ["학습 기록이 없어 계보(서버 스크립트 해시, 데이터 파일 해시, 하이퍼파라미터)가 패키지에 들어가지 않는다. `train`이 쓴 *.train-wrap.json을 --train-record-*로 준다"]
    contract_version = _contract_version(resolved_path(cfg, cfg["inputs"]["schema_dir"]))
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
            run_id=run_id,
            ext={"attached": True, "trainer": "external", "adapter_config_r": confs[r].get("r"),
                                "evaluated": False, "lineage": lineage[r]})
        rep = evaluation_report_build.unevaluated_report(role=r, package={"id": pid, "version": ver, "content_hash": m["content_hash"]},
                                    dataset=dataset, created_at=created, decoding=cfg["serving"][r]["decoding"], report_id=m["eval_report_ref"])
        rb = canonical.canonicalize(rep)
        (out / "package").mkdir(parents=True, exist_ok=True)
        (out / "package" / f"{r}.manifest.json").write_bytes(canonical.canonicalize(m))
        (out / "package" / f"{r}.evaluation-report.json").write_bytes(rb)
        result[r] = {"package_id": pid, "version": ver, "content_hash": m["content_hash"], "adapter_hash": m["adapter_hash"], "published": False}
        if r in warnings:
            result[r]["warnings"] = warnings[r]
        if publish:
            reg = registry
            if reg is None:
                from .stores import make_registry
                reg = make_registry(cfg)
            got = reg.publish(m, ad, rb)
            result[r].update(published=True, content_hash=got["content_hash"])
    return result


def _check_ref(ref: dict) -> None:
    if schemas.errors("DatasetRef", ref):
        raise ConfigError("dataset은 {id, version, content_hash}여야 한다")
