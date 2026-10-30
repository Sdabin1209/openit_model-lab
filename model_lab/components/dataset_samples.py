"""데이터셋 샘플 컴포넌트: 내려받은 데이터셋 파일에서 역할별 분할 샘플을 읽어 계약 샘플 스키마로 검증한다.
입력 DatasetSamplesRequest {manifest, base, role}, 출력 RoleSamples {TRAIN, VALIDATION, EVAL}. 통신하지 않는다(파일은 link_dataset_fetch가 내려받았다)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from boundary_contracts import BoundaryError, validator

from ..wiring.errors import PipelineError

# 2.0.0(D74): 파일 역할은 LORA_A/LORA_B이고 샘플의 input/expected는 샘플 스키마가 직접 검증한다(문진 스키마를 참조하지 않는다).
SAMPLE_CONTRACT = {"LORA_A": "ActionExtractionSample", "LORA_B": "ResponseSample"}


def qs_key(ref: dict) -> tuple:
    return (ref["id"], ref["version"])


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


def load_role_samples(manifest: dict, base: Path, role: str) -> dict[str, list[dict]]:
    """역할의 TRAIN/VALIDATION/EVAL 샘플(계약 샘플 스키마로 검증). 샘플에 question_set이 없고 파일의 문항 세트가 하나면 그것으로 채운다
    (서버 데이터처럼 샘플에 없을 때 평가 쪼개기용). 평가 샘플이 학습·검증과 겹치면 중단하고, 학습 샘플이 없어도 중단한다."""
    splits: dict[str, list[dict]] = {}
    for split in ("TRAIN", "VALIDATION", "EVAL"):
        samples = []
        for f in split_files(manifest, role, split):
            fq = file_question_sets(manifest, f)
            for smp in read_samples(base / f["path"], role):
                if "question_set" not in smp and len(fq) == 1:
                    smp = {**smp, "question_set": fq[0]}
                samples.append(smp)
        splits[split] = samples
    check_no_leak(splits["TRAIN"] + splits["VALIDATION"], splits["EVAL"] + [])
    if not splits["TRAIN"]:
        raise PipelineError(f"{role}: 학습 샘플이 없다")
    return splits
