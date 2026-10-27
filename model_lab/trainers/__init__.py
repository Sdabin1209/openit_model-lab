from __future__ import annotations

from .base import TrainRequest, Trainer  # noqa: F401


def make_trainer(kind: str):
    if kind == "dry-run":
        from .dryrun import DryRunTrainer
        return DryRunTrainer()
    if kind == "hf":
        import warnings
        warnings.warn("trainers/hf.py는 학습 서버 정본(train_lora_a.py, train_lora_b.py)과 다르다(채팅 템플릿 없음, 양자화·모듈 다름). "
                      "정본이 아니다: 여기서 만든 어댑터는 서버 학습과 같지 않으며 파이프라인 검증용이다. 실제 학습은 `model-lab train`(D78, G06).",
                      UserWarning, stacklevel=2)
        from .hf import HFLoraTrainer
        return HFLoraTrainer()
    raise ValueError(kind)
