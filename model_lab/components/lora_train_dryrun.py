"""CPU 검증용 학습 컴포넌트. 입력 TrainRequest, 출력 TrainResult(schemas/). 실제 학습을 하지 않는다. 데이터·설정·시드·스텝에서 결정적으로 '어댑터'를 만든다.

파이프라인의 재현성(같은 입력 -> 같은 adapter_hash)과 체크포인트 재개를 GPU 없이 시험하는 용도다.
모델 품질과 무관하며, 이 학습기로 만든 패키지는 서빙할 수 없다(시험 전용)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from boundary_contracts import canonical

from ..types.TrainRequest_schema import TrainRequest
from ..types.TrainResult_schema import TrainResult


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


def _paths(req) -> dict:
    """TrainRequest(schemas/)의 경로 필드를 Path로 바꾼다. 값 검증은 호출하는 wiring이 스키마로 한다."""
    return {**req, **{k: Path(req[k]) for k in ("train_path", "val_path", "adapter_dir", "work_dir") if req.get(k) is not None}}



class Interrupted(Exception):
    """시험에서 학습 중단을 흉내 낸다."""


class DryRunTrainer:
    name = "dry-run"

    def __init__(self, interrupt_at_step: int | None = None):
        self.interrupt_at_step = interrupt_at_step

    def train(self, req: TrainRequest) -> TrainResult:
        req = _paths(req)
        rows = read_jsonl(req["train_path"])
        t = req["training"]
        if t.get("max_train_samples"):
            rows = rows[: t["max_train_samples"]]
        total = steps_for(len(rows), t)
        every = t.get("checkpoint_every_steps", 50)
        per_step = t.get("per_device_batch_size", 1) * t.get("grad_accum_steps", 1) * t.get("gpu_count", 1)
        req["work_dir"].mkdir(parents=True, exist_ok=True)
        ck = req["work_dir"] / "ckpt.json"
        step, h = 0, canonical.sha256_hash({"seed": req["seed"], "role": req["role"], "base": req["base_model"], "training": t})
        resumed = 0
        if ck.exists():
            c = json.loads(ck.read_text())
            step, h, resumed = c["step"], c["h"], c["step"]
        while step < total:
            ids = [rows[(step * per_step + i) % len(rows)]["sample_id"] for i in range(per_step)]
            h = canonical.sha256_hash({"prev": h, "ids": ids})
            step += 1
            if step % every == 0 or step == total:
                tmp = ck.with_suffix(".tmp")
                tmp.write_text(json.dumps({"step": step, "h": h}))
                os.replace(tmp, ck)
            if self.interrupt_at_step is not None and step >= self.interrupt_at_step and step < total:
                raise Interrupted(f"step {step}")
        req["adapter_dir"].mkdir(parents=True, exist_ok=True)
        (req["adapter_dir"] / "adapter_config.json").write_text(
            json.dumps({"peft_type": "DRY_RUN", "base_model": req["base_model"]["id"], "role": req["role"]}, sort_keys=True))
        # 왜 이 이름: 게시는 ModelPackageLayout(필수 파일 3개)을 검사한다. 모의 학습기의 내용은 safetensors가 아니라 상태 해시 문자열이다.
        (req["adapter_dir"] / "adapter_model.safetensors").write_text(h)
        return {"steps": total, "resumed_from_step": resumed, "base_model_revision": req["base_model"].get("revision", "unknown"),
                "library_versions": {}, "metrics": {"final_state_hash": h}}
