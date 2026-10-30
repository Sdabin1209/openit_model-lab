"""모델 패키저: 어댑터 + 서빙 설정 + 계보 -> ModelPackageManifest(역할별 하나)."""
from __future__ import annotations

import re
from pathlib import Path

import model_runtime
from boundary_contracts import canonical, validator
from boundary_contracts.action_rules import load_rules


from boundary_contracts import PIPELINE_SCHEMA_VERSION


def safe_id(s: str) -> str:
    """ID 규칙(CONVENTIONS 2절)에 맞춘 문자열(버전 이름용). base_model.id에는 쓰지 않는다."""
    return re.sub(r"[^A-Za-z0-9._:-]", "-", s.replace("/", "--"))[:128].lstrip("-._:") or "x"


def training_question_sets(dataset_manifest: dict, role: str) -> list[dict]:
    """학습·검증 파일이 쓴 문항 세트(id, version 순). 파일 역할 이름은 역할 이름과 같다(DatasetManifest.files[].role, D74)."""
    refs: dict = {}
    for f in dataset_manifest["files"]:
        if f["role"] == role and f["split"] in ("TRAIN", "VALIDATION"):
            for r in f.get("question_sets") or dataset_manifest["question_sets"]:
                refs[(r["id"], r["version"])] = dict(r)
    return [refs[k] for k in sorted(refs)]


SYSTEM_PROMPT_FILE = "system_prompt.txt"


def action_rules_version() -> str:
    """계보의 action_rules_version(계약 3.0.0, D109). 값은 설치된 계약의 LoRA-A 출력 규칙 데이터(ActionExtractionRules.rules_version)에서 읽는다.
    왜 읽나: 평가가 쓰는 규칙(check_action_extraction)이 바로 이 데이터라, 코드에 리터럴로 적으면 계약이 바뀔 때 어긋난다."""
    return load_rules()["rules_version"]


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
                   dataset_manifest: dict, run_id: str, ext: dict, system_prompt: dict, precheck=None) -> dict:
    """system_prompt: install_system_prompt가 낸 {path, sha256}. 어댑터 디렉터리 안의 파일과 맞아야 한다."""
    if precheck:      # 게시 때 거절될 디렉터리를 만들기 전에 알린다(같은 검사, 게시 쪽이 정본). model_registry의 검사를 wiring이 주입한다
        precheck(adapter_dir, role, system_prompt)
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
            "action_rules_version": action_rules_version(),
            "model_runtime_version": model_runtime.RUNTIME_VERSION,
            "question_sets": training_question_sets(dataset_manifest, role),
        },
        "eval_report_ref": eval_report_ref(package_id, version),
        "ext": {"model-lab": ext},
    }
    m["content_hash"] = canonical.content_hash(m)
    validator("ModelPackageManifest").validate(m)
    return m

