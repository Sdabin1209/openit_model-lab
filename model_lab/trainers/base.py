"""학습기 인터페이스. 학습 방법은 설정(training)으로 바꾸고 코드는 특정 모델에 의존하지 않는다."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class TrainRequest:
    role: str
    base_model: dict          # {"id": ..., "revision": ...}
    training: dict            # 역할에 적용되는 최종 학습 설정
    seed: int
    train_path: Path          # JSONL {sample_id, prompt, completion}
    val_path: Path | None
    adapter_dir: Path         # 최종 어댑터. 어댑터 파일만 둔다(adapter_hash 대상)
    work_dir: Path            # 체크포인트와 로그. adapter_hash에 들어가지 않는다


class Trainer(Protocol):
    name: str

    def train(self, req: TrainRequest) -> dict:
        """학습하고 어댑터를 req.adapter_dir에 쓴다. 체크포인트가 있으면 이어서 한다.
        돌려주는 dict는 run 기록용이다: steps, resumed_from_step, base_model_revision, library_versions, metrics."""


def read_jsonl(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def steps_for(n_samples: int, training: dict) -> int:
    """총 최적화 스텝 수. max_steps가 있으면 그것(상한), 없으면 에폭으로 계산."""
    import math
    per_step = training.get("per_device_batch_size", 1) * training.get("grad_accum_steps", 1) * training.get("gpu_count", 1)
    total = max(1, math.ceil(n_samples * training.get("epochs", 1) / per_step))
    ms = training.get("max_steps")
    return min(total, ms) if ms else total
