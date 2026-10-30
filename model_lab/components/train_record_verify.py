"""학습 기록 대조 컴포넌트: `train`이 쓴 학습 기록(TrainWrapRecord)을 읽어 어댑터·데이터셋·기반 모델과 맞는지 확인하고 계보(TrainLineage)를 낸다.

입력 TrainRecordCheckRequest {role, path, adapter_dir, base, dataset}, 출력 TrainLineage. 기록 형식(record_version, 필수 필드)은 스키마 TrainWrapRecord가 맡는다."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..wiring.errors import PipelineError

ADAPTER_FILES = ("adapter_model.safetensors", "adapter_config.json")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def read_train_record(role: str, path, *, adapter_dir: Path, base: dict, dataset: dict) -> dict:
    """`train`가 쓴 학습 기록(`*.train-wrap.json`)을 읽어 어댑터·데이터셋·기반 모델과 맞는지 확인하고 패키지에 넣을 계보를 돌려준다(ML-5).
    어긋나면 PipelineError. 확인하지 못하는 것: 기록이 진짜 서버 실행의 결과인지(기록은 학습 실행기가 쓴 자기 보고다)."""
    path = Path(path)
    try:
        raw = path.read_bytes()
        rec = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        raise PipelineError(f"{role}: 학습 기록을 읽을 수 없다: {path.name}") from None
    if not isinstance(rec, dict) or rec.get("record_version") != 1:   # 전체 스키마(TrainWrapRecord) 검증으로 바꾸지 못했다: schemas/README.md "스키마 밖 조건" 3번
        raise PipelineError(f"{role}: 학습 기록 형식이 아니다(record_version 1)")
    if rec.get("role") != role:
        raise PipelineError(f"{role}: 학습 기록의 역할({rec.get('role')})이 다르다")
    rd = rec.get("dataset") or {}
    for k in ("id", "version"):
        if rd.get(k) != dataset[k]:
            raise PipelineError(f"{role}: 학습 기록의 dataset.{k}({rd.get(k)!r})가 입력({dataset[k]!r})과 다르다")
    if rd.get("content_hash") and rd["content_hash"] != dataset["content_hash"]:
        raise PipelineError(f"{role}: 학습 기록의 dataset.content_hash가 입력과 다르다")
    if (rec.get("base_model") or {}).get("id") != base["id"]:
        raise PipelineError(f"{role}: 학습 기록의 기반 모델이 {base['id']}와 다르다")
    for f, want in ((rec.get("adapter") or {}).get("files") or {}).items():
        if f in ADAPTER_FILES and _sha256(Path(adapter_dir) / f) != want:
            raise PipelineError(f"{role}: 학습 기록의 {f} 해시가 지금 어댑터 파일과 다르다(기록 뒤에 바뀌었거나 다른 실행의 기록이다)")
    return {"train_record_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(), "config_hash": rec.get("config_hash"),
            "server_script_sha256": rec.get("server_script_sha256"), "dataset_file_sha256": rd.get("file_sha256"),
            "base_model": rec.get("base_model"), "hyperparams": rec.get("hyperparams")}
