"""데이터셋 읽기(I2). 읽기 SDK(DatasetStore)로만 읽고, 해시·스키마·분할을 다시 확인한다."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from boundary_contracts import BoundaryError, canonical, validator

from .config import ROLE_TO_FILE_ROLE
from .errors import PipelineError

# 2.0.0(D74): 파일 역할은 LORA_A/LORA_B이고 샘플의 input/expected는 샘플 스키마가 직접 검증한다(문진 스키마를 참조하지 않는다).
SAMPLE_CONTRACT = {"LORA_A": "ActionExtractionSample", "LORA_B": "ResponseSample"}


def ref_of(manifest: dict) -> dict:
    return {"id": manifest["dataset_id"], "version": manifest["version"], "content_hash": manifest["content_hash"]}


def qs_key(ref: dict) -> tuple:
    return (ref["id"], ref["version"])


def resolve_version(store, dataset_id: str, version: str | None) -> str:
    """version이 없으면 가장 최근(list_versions는 오래된 것부터이므로 마지막)."""
    if version:
        return version
    versions = store.list_versions(dataset_id)
    if not versions:
        raise BoundaryError("NOT_FOUND", "게시된 데이터셋 버전이 없다")
    return versions[-1]


def fetch_dataset(store, dataset_id: str, version: str, dest: Path, *, contract_version: int) -> dict:
    """매니페스트를 읽고 모든 파일을 dest에 내려받아 해시를 확인한다. 읽은 매니페스트를 낸다."""
    manifest = store.get_manifest(dataset_id, version)
    try:
        validator("DatasetManifest").validate(manifest)
    except Exception:
        raise BoundaryError("INVALID_RECORD", "DatasetManifest 위반") from None
    if canonical.content_hash(manifest) != manifest["content_hash"]:
        raise BoundaryError("INTEGRITY_ERROR", "DatasetManifest의 content_hash가 맞지 않는다")
    if manifest["runtime_contract_version"] != contract_version:
        raise BoundaryError("UNSUPPORTED_VERSION", "데이터셋의 문진 스키마 정수 버전이 다르다")
    dest.mkdir(parents=True, exist_ok=True)
    for f in manifest["files"]:
        out = dest / f["path"]
        out.parent.mkdir(parents=True, exist_ok=True)
        with store.open_file(dataset_id, version, f["path"]) as src, open(out, "wb") as dst:
            for chunk in iter(lambda: src.read(1 << 20), b""):
                dst.write(chunk)
        if canonical.file_hash(out) != f["content_hash"]:
            raise BoundaryError("INTEGRITY_ERROR", "데이터셋 파일의 해시가 맞지 않는다")
    return manifest


def read_samples(path: Path, file_role: str, schema_dir: Path | None = None) -> Iterator[dict]:
    """JSON Lines 샘플을 읽으며 경계 계약(ActionExtractionSample/ResponseSample)으로 검증한다. schema_dir은 쓰지 않는다(호환용)."""
    vs = validator(SAMPLE_CONTRACT[file_role])
    with open(path, "r", encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                s = json.loads(line)
                vs.validate(s)
            except Exception as e:
                # 발화 원문을 오류에 담지 않는다. 줄 번호와 위반 종류만.
                kind = getattr(e, "validator", type(e).__name__)
                raise BoundaryError("INVALID_RECORD", f"{path.name}:{n} 샘플 위반({kind})") from None
            yield s


def split_files(manifest: dict, file_role: str, split: str) -> list[dict]:
    return [f for f in manifest["files"] if f["role"] == file_role and f["split"] == split]


def file_question_sets(manifest: dict, f: dict) -> list[dict]:
    return f.get("question_sets") or manifest["question_sets"]


def seen_question_sets(manifest: dict, file_role: str) -> set:
    """학습·검증 파일이 쓴 문항 세트(id, version). 이 밖의 문항 세트가 평가에 나오면 보지 못한 문항이다(D32)."""
    seen: set = set()
    for split in ("TRAIN", "VALIDATION"):
        for f in split_files(manifest, file_role, split):
            seen |= {qs_key(r) for r in file_question_sets(manifest, f)}
    return seen


def check_no_leak(train_val: list[dict], eval_samples: list[dict]) -> None:
    """평가 샘플이 학습·검증과 시나리오·샘플 ID를 공유하면 중단한다(브리프 4절). I2도 확인하지만 다시 본다."""
    tv_scn = {s["scenario_id"] for s in train_val}
    tv_ids = {s["sample_id"] for s in train_val}
    for s in eval_samples:
        if s["scenario_id"] in tv_scn or s["sample_id"] in tv_ids:
            raise PipelineError("평가 샘플이 학습·검증과 시나리오 또는 샘플 ID를 공유한다. 학습을 중단한다.")
