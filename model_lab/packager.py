"""모델 패키저: 어댑터 + 서빙 설정 + 계보 -> ModelPackageManifest(역할별 하나)."""
from __future__ import annotations

import re
from pathlib import Path

import model_runtime
from boundary_contracts import canonical, validator

from .datasets import file_question_sets, split_files, qs_key
from .config import ROLE_TO_FILE_ROLE
from .registry import layout_of, verify_system_prompt

from boundary_contracts import PIPELINE_SCHEMA_VERSION


def safe_id(s: str) -> str:
    """ID 규칙(CONVENTIONS 2절)에 맞춘 문자열(버전 이름용). base_model.id에는 쓰지 않는다."""
    return re.sub(r"[^A-Za-z0-9._:-]", "-", s.replace("/", "--"))[:128].lstrip("-._:") or "x"


def training_question_sets(dataset_manifest: dict, role: str) -> list[dict]:
    refs: dict = {}
    fr = ROLE_TO_FILE_ROLE[role]
    for split in ("TRAIN", "VALIDATION"):
        for f in split_files(dataset_manifest, fr, split):
            for r in file_question_sets(dataset_manifest, f):
                refs[qs_key(r)] = dict(r)
    return [refs[k] for k in sorted(refs)]


SYSTEM_PROMPT_FILE = "system_prompt.txt"
# 왜: 계약 기록(DatasetManifest, ModelPackageManifest.lineage)에 turn_engine_version 필드가 남아 있다(D109). turn-engine 코드를 쓰지 않으므로
# 값은 실행한 코드에서 읽을 수 없고, 마지막 릴리스 0.4.3을 고정 표지로 쓴다(v 없음: release-gate CONTRACT_COMPAT가 설치 패키지 버전 문자열과 전체 문자열로 비교한다)(3.0.0에서 필드가 바뀐다). 설정 lineage.turn_engine_version으로 바꿀 수 있다.
DEFAULT_TURN_ENGINE_MARK = "0.4.3"


def install_system_prompt(adapter_dir: Path, text: str, filename: str = SYSTEM_PROMPT_FILE) -> dict:
    """시스템 프롬프트를 어댑터 디렉터리 안에 쓰고 ModelPackageManifest.system_prompt{path, sha256}를 낸다(D75).
    adapter_hash가 파일 포함 전체 디렉터리 해시이므로 build_manifest보다 먼저 불러야 한다. 내용은 바이트 그대로(UTF-8)."""
    adapter_dir = Path(adapter_dir)
    (adapter_dir / filename).write_bytes(text.encode("utf-8"))
    return {"path": filename, "sha256": canonical.file_hash(adapter_dir / filename)}


def eval_report_ref(package_id: str, version: str) -> str:
    """패키지마다 정해지는 ID. 보고서가 패키지의 content_hash를 담고 매니페스트가 이 ref를 담으므로
    보고서 내용에서 ref를 정하면 순환한다."""
    return safe_id(f"eval-{package_id}.{version}")


def build_manifest(*, role: str, package_id: str, version: str, created_at: str, contract_version: int,
                   base_model: dict, adapter_dir: Path, serving_config: dict, dataset_ref: dict,
                   dataset_manifest: dict, run_id: str, ext: dict, system_prompt: dict,
                   turn_engine_version: str = DEFAULT_TURN_ENGINE_MARK) -> dict:
    """system_prompt: install_system_prompt가 낸 {path, sha256}. 어댑터 디렉터리 안의 파일과 맞아야 한다."""
    model_runtime.validate_serving_config(serving_config)
    verify_system_prompt(adapter_dir, system_prompt)
    layout_of(adapter_dir, role)      # 게시 때 거절될 디렉터리를 만들기 전에 알린다(같은 검사, 게시 쪽이 정본)
    m = {
        "pipeline_schema_version": PIPELINE_SCHEMA_VERSION,
        "package_id": package_id,
        "version": version,
        "role": role,
        "created_at": created_at,
        "runtime_contract_version": contract_version,
        "base_model": {"id": base_model["id"], "revision": base_model["revision"]},     # 계약 v1.2.0: Hub 이름의 '/' 허용
        "adapter_hash": canonical.directory_hash(adapter_dir),
        "serving_config": serving_config,
        "serving_config_hash": model_runtime.serving_config_hash(serving_config),
        "system_prompt": {"path": system_prompt["path"], "sha256": system_prompt["sha256"]},
        "lineage": {
            "dataset": dataset_ref,
            "training_run_id": run_id,
            "turn_engine_version": turn_engine_version,
            "model_runtime_version": model_runtime.RUNTIME_VERSION,
            "question_sets": training_question_sets(dataset_manifest, role),
        },
        "eval_report_ref": eval_report_ref(package_id, version),
        "ext": {"model-lab": ext},
    }
    m["content_hash"] = canonical.content_hash(m)
    validator("ModelPackageManifest").validate(m)
    return m

