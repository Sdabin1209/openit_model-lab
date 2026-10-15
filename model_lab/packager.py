"""모델 패키저: 어댑터 + 서빙 설정 + 계보 -> ModelPackageManifest(역할별 하나)."""
from __future__ import annotations

import re
from pathlib import Path

import model_runtime
from boundary_contracts import canonical, validator

from .datasets import file_question_sets, split_files, qs_key
from .config import ROLE_TO_FILE_ROLE

PIPELINE_SCHEMA_VERSION = "1.1"


def safe_id(s: str) -> str:
    """ID 규칙(CONVENTIONS 2절)에 맞춘다. '/'는 '--'로(HF 저장소 이름 관례)."""
    return re.sub(r"[^A-Za-z0-9._:-]", "-", s.replace("/", "--"))[:128].lstrip("-._:") or "x"


def training_question_sets(dataset_manifest: dict, role: str) -> list[dict]:
    refs: dict = {}
    fr = ROLE_TO_FILE_ROLE[role]
    for split in ("TRAIN", "VALIDATION"):
        for f in split_files(dataset_manifest, fr, split):
            for r in file_question_sets(dataset_manifest, f):
                refs[qs_key(r)] = dict(r)
    return [refs[k] for k in sorted(refs)]


def eval_report_ref(report: bytes) -> str:
    """내용 주소 방식 ID: 같은 보고서는 같은 ref."""
    return "eval-" + canonical.sha256_hash(report.hex())[7:39]


def build_manifest(*, role: str, package_id: str, version: str, created_at: str, contract_version: int,
                   base_model: dict, adapter_dir: Path, serving_config: dict, dataset_ref: dict,
                   dataset_manifest: dict, run_id: str, eval_report: bytes, ext: dict) -> dict:
    model_runtime.validate_serving_config(serving_config)
    m = {
        "pipeline_schema_version": PIPELINE_SCHEMA_VERSION,
        "package_id": package_id,
        "version": version,
        "role": role,
        "created_at": created_at,
        "runtime_contract_version": contract_version,
        "base_model": {"id": safe_id(base_model["id"]), "revision": base_model["revision"]},
        "adapter_hash": canonical.directory_hash(adapter_dir),
        "serving_config": serving_config,
        "serving_config_hash": model_runtime.serving_config_hash(serving_config),
        "lineage": {
            "dataset": dataset_ref,
            "training_run_id": run_id,
            "turn_engine_version": _te_version(),
            "model_runtime_version": model_runtime.RUNTIME_VERSION,
            "question_sets": training_question_sets(dataset_manifest, role),
        },
        "eval_report_ref": eval_report_ref(eval_report),
        "ext": {"model-lab": ext},
    }
    m["content_hash"] = canonical.content_hash(m)
    validator("ModelPackageManifest").validate(m)
    return m


def _te_version() -> str:
    import turn_engine
    return turn_engine.ENGINE_VERSION
