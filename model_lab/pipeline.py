"""설정 파일 하나로 도는 파이프라인: 데이터셋(I2) -> 변환 -> 학습 -> 평가 -> 패키징 -> 게시(I3).

재현성: run_id는 (설정 해시, 데이터셋 ref, 시드, 라이브러리 버전)에서 정해진다. 같은 입력이면 같은 run 디렉터리를
가리키므로 이미 끝난 단계는 건너뛰고(멱등), 중간에 멈췄으면 끝난 단계 다음부터, 학습은 체크포인트부터 이어 한다.
시각은 기록에만 있고 run_id·해시 계산에 들어가지 않는다(패키지의 created_at은 처음 정해진 값을 run 기록에 고정한다).
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Callable

import model_runtime
from boundary_contracts import BoundaryError, canonical

from . import datasets, evaluate, factories, metrics, packager, regression, schemas
from .config import ROLE_TO_FILE_ROLE, ROLES, config_hash, resolved_path, role_training, system_prompt_text
from .errors import ConfigError, PipelineError
from .registry import FileModelRegistry
from .trainers import TrainRequest, make_trainer
from .transform import render_output, to_example

STAGES = ("load_dataset", "transform", "train", "evaluate", "package", "publish")


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def library_versions() -> dict:
    import turn_engine
    from . import __version__
    v = {"boundary-contracts": metadata.version("boundary-contracts"), "turn-engine": turn_engine.ENGINE_VERSION,
         "model-runtime": model_runtime.RUNTIME_VERSION, "model-lab": __version__}
    return v


def _extra_versions() -> dict:
    out = {"python": sys.version.split()[0]}
    for n in ("torch", "transformers", "peft", "accelerate", "bitsandbytes", "pyyaml", "jsonschema"):
        try:
            out[n] = metadata.version(n)
        except metadata.PackageNotFoundError:
            pass
    return out


def _atomic_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def make_dataset_store(cfg: dict):
    """읽기 대상(I2). kind=http = 원격 dataset-store(D100, 기본), factory = 임의 구현(시험 대역·로컬 파일 읽기)."""
    spec = cfg["inputs"]["dataset_store"]
    if spec.get("kind") == "http":
        if "base_url" not in spec and "base_url_env" not in spec:
            raise ConfigError("inputs.dataset_store.kind=http에는 base_url 또는 base_url_env가 필요하다")
        return factories.build_http("dataset_store", spec)
    return factories.build(spec)


def make_registry(cfg: dict):
    """게시 대상(I3). file = 로컬 파일 구현, http = 원격 model-registry, factory = 임의 구현."""
    spec = cfg["registry"]
    if spec["kind"] == "file":
        return FileModelRegistry(resolved_path(cfg, spec.get("path", "registry")))
    if spec["kind"] == "http":
        if "base_url" not in spec and "base_url_env" not in spec:
            raise ConfigError("registry.kind=http에는 base_url 또는 base_url_env가 필요하다")
        return factories.build_http("model_registry", spec)
    if "factory" not in spec:
        raise ConfigError("registry.kind=factory에는 factory가 필요하다")
    return factories.build(spec)


class Run:
    """한 번의 실행. run.json이 설정, 시드, 데이터셋 버전, 라이브러리 버전, 단계별 결과를 기록한다."""

    def __init__(self, cfg: dict, *, dataset_store=None, registry=None, trainer_factory: Callable | None = None, failure_store=None,
                 clock: Callable[[], str] = now_utc, log: Callable[[str], None] = lambda s: print(s, flush=True)):
        self.cfg = cfg
        if set(cfg["roles"]) != set(ROLES) or set(cfg["serving"]) != set(ROLES):
            raise ConfigError("roles와 serving에 LORA_A, LORA_B가 모두 있어야 한다(model-runtime.load가 두 역할을 함께 받는다)")
        self.store = dataset_store or make_dataset_store(cfg)
        self.registry = registry or make_registry(cfg)
        self.trainer_factory = trainer_factory or (lambda kind: make_trainer(kind))
        self.clock, self.log = clock, log
        self._failure_store = failure_store
        self._rset = None
        self.schema_dir = resolved_path(cfg, cfg["inputs"]["schema_dir"])
        self.contract_version = schemas.contract_version(self.schema_dir)
        self.runs_dir = resolved_path(cfg, cfg["run"].get("runs_dir", "runs"))
        self.seed = cfg["run"]["seed"]
        # 데이터셋 버전 확정과 run_id
        ds = cfg["inputs"]["dataset"]
        self.dataset_id = ds["id"]
        self.dataset_version = datasets.resolve_version(self.store, ds["id"], ds.get("version"))
        dm = self.store.get_manifest(self.dataset_id, self.dataset_version)
        self.dataset_ref = datasets.ref_of(dm)
        self.config_hash = config_hash(cfg)
        self.libs = library_versions()
        key = canonical.sha256_hash({"config": self.config_hash, "dataset": self.dataset_ref,
                                     "seed": self.seed, "libs": self.libs})
        self.run_key = key
        self.run_id = "run-" + key[7:23]
        self.dir = self.runs_dir / self.run_id
        self.rec_path = self.dir / "run.json"
        self.rec = self._load_or_init()

    # ---- 기록 ----
    def _load_or_init(self) -> dict:
        if self.rec_path.exists():
            return json.loads(self.rec_path.read_text(encoding="utf-8"))
        rec = {"run_id": self.run_id, "run_key": self.run_key, "name": self.cfg["run"].get("name"),
               "created_at": self.clock(), "seed": self.seed, "config_hash": self.config_hash,
               "config": {k: v for k, v in self.cfg.items() if k != "_base_dir"},
               "dataset": self.dataset_ref, "libraries": self.libs, "environment": _extra_versions(),
               "stages": {}, "result": None}
        self.dir.mkdir(parents=True, exist_ok=True)
        _atomic_json(self.rec_path, rec)
        return rec

    def _save(self) -> None:
        _atomic_json(self.rec_path, self.rec)

    def _done(self, *path: str) -> bool:
        d = self.rec["stages"]
        for p in path:
            d = d.get(p, {})
        return d.get("status") == "done"

    def _mark(self, stage: str, role: str | None, **info) -> None:
        d = self.rec["stages"].setdefault(stage, {})
        if role:
            d = d.setdefault(role, {})
        d.update(info)
        self._save()

    # ---- 실행 ----
    def is_complete(self) -> bool:
        return self.rec.get("result") is not None and self.rec["result"].get("status") == "complete"

    def run(self, *, stop_after: str | None = None) -> dict:
        if stop_after is not None and stop_after not in STAGES:
            raise ConfigError(f"알 수 없는 단계: {stop_after}")
        publish = self.cfg.get("publish", {}).get("enabled", True)
        try:
            for stage in STAGES:
                if stage == "publish" and not publish:
                    break
                getattr(self, f"stage_{stage}")()
                if stage == stop_after:
                    self.log(f"[{self.run_id}] {stage} 뒤에서 멈춘다")
                    return self.rec
            self.rec["result"] = {"status": "complete" if publish else "complete-unpublished",
                                  "packages": self.rec["stages"].get("publish", {}).get("packages", {})}
            if not publish:
                self.rec["result"]["status"] = "complete"
            self._save()
        except Exception as e:
            self.rec["last_error"] = {"type": type(e).__name__, "code": getattr(e, "code", None), "message": str(e)[:300]}
            self._save()
            raise
        return self.rec

    # 1. 데이터셋
    def stage_load_dataset(self) -> None:
        raw = self.dir / "dataset"
        if self._done("load_dataset") and (raw / "manifest.json").exists():
            return
        self.log(f"[{self.run_id}] 데이터셋 {self.dataset_id}@{self.dataset_version} 읽기")
        if raw.exists():
            shutil.rmtree(raw)
        dm = datasets.fetch_dataset(self.store, self.dataset_id, self.dataset_version, raw / "files",
                                    contract_version=self.contract_version)
        if datasets.ref_of(dm) != self.dataset_ref:
            raise BoundaryError("INTEGRITY_ERROR", "데이터셋 매니페스트가 실행 중에 달라졌다")
        _atomic_json(raw / "manifest.json", dm)
        self._mark("load_dataset", None, status="done", files=len(dm["files"]), at=self.clock())

    def _manifest(self) -> dict:
        return json.loads((self.dir / "dataset" / "manifest.json").read_text(encoding="utf-8"))

    # 2. 학습 데이터 변환(render_prompt, 시스템 프롬프트는 설정의 파일)
    def stage_transform(self) -> None:
        if self._done("transform"):
            return
        dm = self._manifest()
        base = self.dir / "dataset" / "files"
        for role in ROLES:
            fr = ROLE_TO_FILE_ROLE[role]
            out = self.dir / "data" / role
            out.mkdir(parents=True, exist_ok=True)
            serving = self.cfg["serving"][role]
            model_runtime.validate_serving_config(serving)
            sysp = system_prompt_text(self.cfg, role)
            tv_samples, splits = [], {}
            for split in ("TRAIN", "VALIDATION", "EVAL"):
                samples = []
                for f in datasets.split_files(dm, fr, split):
                    fq = datasets.file_question_sets(dm, f)
                    for smp in datasets.read_samples(base / f["path"], fr, self.schema_dir):
                        if "question_set" not in smp and len(fq) == 1:   # 서버 데이터처럼 샘플에 없으면 파일의 문항 세트(하나일 때)로 채운다(평가 쪼개기용)
                            smp = {**smp, "question_set": fq[0]}
                        samples.append(smp)
                splits[split] = samples
            datasets.check_no_leak(splits["TRAIN"] + splits["VALIDATION"], splits["EVAL"] + [])
            if not splits["TRAIN"]:
                raise PipelineError(f"{role}: 학습 샘플이 없다")
            labels: dict = {}
            for split, name in (("TRAIN", "train"), ("VALIDATION", "val")):
                with open(out / f"{name}.jsonl", "w", encoding="utf-8") as fh:
                    for s in splits[split]:   # 학습·검증에는 해당 분할만 들어간다. EVAL은 여기에 쓰지 않는다.
                        ex = to_example(role, s, serving, sysp, self.schema_dir)
                        if name == "train":
                            labels.setdefault(ex["prompt"], set()).add(ex["completion"])
                        fh.write(json.dumps(ex, ensure_ascii=False, sort_keys=True) + "\n")
            conflicts = sum(1 for v in labels.values() if len(v) > 1)   # 같은 입력에 다른 정답(데이터셋 품질 정보, 중단하지 않는다)
            for split in ("EVAL", "VALIDATION"):
                with open(out / f"{split.lower()}_samples.jsonl", "w", encoding="utf-8") as fh:
                    for s in splits[split]:
                        fh.write(json.dumps(s, ensure_ascii=False, sort_keys=True) + "\n")
            self._mark("transform", role, status="done", train=len(splits["TRAIN"]), validation=len(splits["VALIDATION"]),
                       eval=len(splits["EVAL"]), conflicting_train_prompts=conflicts, train_file_hash=canonical.file_hash(out / "train.jsonl"))
        self.rec["stages"]["transform"]["status"] = "done"
        self._save()

    # 3. 학습
    def stage_train(self) -> None:
        for role in ROLES:
            adapter = self.dir / "adapters" / role
            info = self.rec["stages"].get("train", {}).get(role, {})
            if info.get("status") == "done" and adapter.is_dir() and canonical.directory_hash(adapter) == info["adapter_hash"]:
                continue
            tr = role_training(self.cfg, role)
            trainer = self.trainer_factory(tr.get("trainer", "hf"))
            base = {"id": self.cfg["base_model"]["id"], "revision": self.cfg["base_model"].get("revision", "unspecified")}
            d = self.dir / "data" / role
            req = TrainRequest(role=role, base_model=base, training=tr, seed=self.seed, train_path=d / "train.jsonl",
                               val_path=d / "val.jsonl", adapter_dir=adapter, work_dir=self.dir / "work" / role)
            self.log(f"[{self.run_id}] {role} 학습 시작({trainer.name})")
            self._mark("train", role, status="running", started_at=self.clock())
            res = trainer.train(req)
            # 시스템 프롬프트는 어댑터와 짝이다(D75). 어댑터 디렉터리에 넣은 뒤 해시를 계산하므로 평가·패키징이 같은 해시를 본다.
            packager.install_system_prompt(adapter, system_prompt_text(self.cfg, role))
            self._mark("train", role, status="done", adapter_hash=canonical.directory_hash(adapter),
                       trainer=trainer.name, result=res, finished_at=self.clock())
        self.rec["stages"]["train"]["status"] = "done"
        self._save()

    # 4. 평가
    def _regression_enabled(self) -> bool:
        return bool(self.cfg["evaluation"].get("regression", {}).get("enabled"))

    def _regression_set(self) -> dict:
        if self._rset is None:
            store = self._failure_store
            if store is None:
                if "failure_store" not in self.cfg["inputs"]:
                    raise ConfigError("evaluation.regression.enabled에는 inputs.failure_store가 필요하다")
                store = factories.build(self.cfg["inputs"]["failure_store"])
            self._rset = regression.build_set(store, self.schema_dir, limit=self.cfg["evaluation"]["regression"].get("limit_cases"),
                                              max_listed=self.cfg["evaluation"].get("max_listed_failures", 20))
            self.log(f"[{self.run_id}] 회귀 집합 {self.cfg['evaluation']['regression'].get('label', '')} "
                     f"사례 {self._rset['cases_total']}건, 건너뜀 { {k: v['count'] for k, v in self._rset['skipped'].items()} }")
        return self._rset

    def _backend(self, adapters: dict, dm: dict):
        ev = self.cfg["evaluation"]["backend"]
        if ev["kind"] == "hf":
            from .backends.hf import HFAdapterBackend
            base = {"id": self.cfg["base_model"]["id"], "revision": self.cfg["base_model"].get("revision")}
            return HFAdapterBackend(base, adapters, role_training(self.cfg, "LORA_A"))
        from .backends.fake import oracle_backend
        mapping = {}
        for role in ROLES:
            extra = self._regression_set()["samples"][role] if self._regression_enabled() else []
            for s in self._eval_samples(role) + extra:
                mapping[model_runtime.render_prompt(role, s["input"], serving_config=self.cfg["serving"][role],
                                                    system_prompt=system_prompt_text(self.cfg, role),
                                                    schema_dir=self.schema_dir)] = render_output(role, s["expected"])
        feats = self.cfg["serving"]["LORA_A"]["constraint"]["supported_features"]
        return oracle_backend(mapping, corrupt_every=ev.get("corrupt_every", 0), supported_features=feats)

    def _eval_samples(self, role: str) -> list[dict]:
        split = self.cfg["evaluation"].get("split", "EVAL")
        p = self.dir / "data" / role / f"{split.lower()}_samples.jsonl"
        rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        lim = self.cfg["evaluation"].get("limit_samples")
        return rows[:lim] if lim else rows

    def stage_evaluate(self) -> None:
        if self._done("evaluate") and all((self.dir / "eval" / f"report-{r}.json").exists() for r in ROLES):
            return
        dm = self._manifest()
        adapters = {r: self.dir / "adapters" / r for r in ROLES}
        hashes = {r: model_runtime.serving_config_hash(self.cfg["serving"][r]) for r in ROLES}
        backend = self._backend(adapters, dm)
        prompts = {r: {"path": packager.SYSTEM_PROMPT_FILE, "sha256": canonical.file_hash(adapters[r] / packager.SYSTEM_PROMPT_FILE)} for r in ROLES}
        session = model_runtime.load(adapters, {r: self.cfg["serving"][r] for r in ROLES}, schema_dir=self.schema_dir,
                                     expected_serving_config_hashes=hashes, system_prompts=prompts, backend=backend)
        split = self.cfg["evaluation"].get("split", "EVAL")
        for role in ROLES:   # 평가는 온도 0(결정적)에서 한다(D48). 온도 > 0의 재현성은 약속되지 않는다.
            if self.cfg["serving"][role]["decoding"]["temperature"] != 0:
                raise PipelineError(f"{role}: 평가는 serving_config의 온도 0에서만 한다")
        for role in ROLES:
            fr = ROLE_TO_FILE_ROLE[role]
            samples = self._eval_samples(role)
            if not samples:
                raise PipelineError(f"{role}: 평가 샘플이 없다({split})")
            seen = datasets.seen_question_sets(dm, fr)
            files = [{"path": f["path"], "question_sets": [f"{r['id']}@{r['version']}" for r in datasets.file_question_sets(dm, f)],
                      "unseen": not {datasets.qs_key(r) for r in datasets.file_question_sets(dm, f)} <= seen}
                     for f in datasets.split_files(dm, fr, split)]
            ctx = {"run_id": self.run_id, "dataset": self.dataset_ref, "split": split, "seed": self.seed,
                   "adapter_hash": self.rec["stages"]["train"][role]["adapter_hash"],
                   "serving_config_hash": hashes[role], "libraries": self.libs,
                   "evaluation_backend": {"kind": self.cfg["evaluation"]["backend"]["kind"], "name": backend.name,
                                          "version": backend.version, "constrained_decoding": False}}
            self.log(f"[{self.run_id}] {role} 평가 {len(samples)}건")
            rep, pairs = evaluate.build_report(role, session, samples, seen_sets=seen, eval_files=files, return_records=True,
                                               max_listed_failures=self.cfg["evaluation"].get("max_listed_failures", 20), context=ctx)
            rrecs = None
            if self._regression_enabled():
                rset = self._regression_set()
                rpairs = regression.evaluate_set(role, session, rset)
                rrecs = [r for _, r in rpairs]
                rep["regression"] = regression.section(role, session, rset, recs=rpairs,
                                                       max_listed=self.cfg["evaluation"].get("max_listed_failures", 20))
            else:
                rep["regression"] = {"enabled": False}
            rep["evaluation_report_metrics"], rep["evaluation_report_notes"] = metrics.build_metrics(role, pairs, seen, regression_recs=rrecs,
                                                                                           constrained_decoding=ctx["evaluation_backend"]["constrained_decoding"])
            out = self.dir / "eval" / f"report-{role}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(evaluate.report_bytes(rep))
            self._mark("evaluate", role, status="done", report_hash=canonical.file_hash(out), samples=len(samples))
        self.rec["stages"]["evaluate"]["status"] = "done"
        self._save()

    # 5. 패키징
    def stage_package(self) -> None:
        if self._done("package"):
            return
        mr = self.cfg.get("checks", {}).get("max_lora_rank")     # 대상 엔진 프로파일의 상한(D48). 게시 전 확인
        if mr is not None:
            for role in ROLES:
                r = json.loads((self.dir / "adapters" / role / "adapter_config.json").read_text()).get("r")
                if r is None or r > mr:
                    raise PipelineError(f"{role}: 어댑터 순위 {r}가 대상 엔진의 max_lora_rank {mr}를 넘는다")
        dm = self._manifest()
        st = self.rec["stages"].setdefault("package", {})
        created = st.get("created_at") or self.clock()      # 처음 정한 값을 고정한다(재실행해도 같은 매니페스트)
        version = packager.safe_id(f"{self.dataset_version}-{self.run_key[7:15]}")
        for role in ROLES:
            detail = json.loads((self.dir / "eval" / f"report-{role}.json").read_text(encoding="utf-8"))
            tr = self.rec["stages"]["train"][role]
            base = {"id": self.cfg["base_model"]["id"],
                    "revision": tr["result"].get("base_model_revision") or self.cfg["base_model"].get("revision", "unspecified")}
            sp = packager.install_system_prompt(self.dir / "adapters" / role, system_prompt_text(self.cfg, role))
            m = packager.build_manifest(
                role=role, system_prompt=sp, package_id=self.cfg["roles"][role]["package_id"], version=version, created_at=created,
                contract_version=self.contract_version, base_model=base, adapter_dir=self.dir / "adapters" / role,
                serving_config=self.cfg["serving"][role], dataset_ref=self.dataset_ref, dataset_manifest=dm,
                run_id=self.run_id,
                ext={"run_id": self.run_id, "config_hash": self.config_hash, "seed": self.seed,
                     "trainer": tr["trainer"],
                     "resumed": tr["result"].get("resumed_from_step", 0) > 0,
                     "resumed_from_step": tr["result"].get("resumed_from_step", 0),
                     "train_data_hash": self.rec["stages"]["transform"][role]["train_file_hash"],
                     "eval_detail_hash": canonical.file_hash(self.dir / "eval" / f"report-{role}.json")})
            _atomic_json(self.dir / "package" / f"{role}.manifest.json", m)
            # 게시하는 평가 리포트 = EvaluationReport(지표 체계, 합격 판정·임계치 없음). 자세한 쪼개 보기는 ext에 둔다.
            ev = metrics.evaluation_report(
                role=role, report_id=m["eval_report_ref"], created_at=created,
                package={"id": m["package_id"], "version": m["version"], "content_hash": m["content_hash"]},
                dataset=self.dataset_ref, question_sets=m["lineage"]["question_sets"],
                backend=detail["context"]["evaluation_backend"], decoding=self.cfg["serving"][role]["decoding"],
                metrics=detail.pop("evaluation_report_metrics"), notes=detail.pop("evaluation_report_notes"), detail=detail)
            (self.dir / "package" / f"{role}.evaluation-report.json").write_bytes(canonical.canonicalize(ev))
            st[role] = {"status": "done", "package_id": m["package_id"], "version": version, "content_hash": m["content_hash"],
                        "adapter_hash": m["adapter_hash"]}
        st["created_at"] = created
        st["status"] = "done"
        self._save()

    # 6. 게시(I3)
    def stage_publish(self) -> None:
        st = self.rec["stages"].setdefault("publish", {})
        pk = {}
        for role in ROLES:
            m = json.loads((self.dir / "package" / f"{role}.manifest.json").read_text(encoding="utf-8"))
            report = (self.dir / "package" / f"{role}.evaluation-report.json").read_bytes()
            got = self.registry.publish(m, self.dir / "adapters" / role, report)   # 멱등: 같은 내용이면 기존 것
            pk[role] = {"package_id": got["package_id"], "version": got["version"], "content_hash": got["content_hash"]}
            self.log(f"[{self.run_id}] 게시 {got['package_id']}@{got['version']}")
        st.update(status="done", packages=pk, at=self.clock())
        self._save()


def run_once(cfg: dict, **kw) -> dict:
    """저장소를 보고 일하는 한 번의 실행(D39). 최신 데이터셋 버전에 대해 이미 끝난 실행이면 아무것도 하지 않는다."""
    r = Run(cfg, **kw)
    if r.is_complete():
        r.log(f"[{r.run_id}] 이미 끝난 실행. 할 일이 없다")
        return {"action": "skip", "run_id": r.run_id, "dataset_version": r.dataset_version}
    r.run()
    return {"action": "ran", "run_id": r.run_id, "dataset_version": r.dataset_version}
