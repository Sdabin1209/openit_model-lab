from __future__ import annotations

from .base import TrainRequest, Trainer  # noqa: F401


def make_trainer(kind: str):
    if kind == "dry-run":
        from .dryrun import DryRunTrainer
        return DryRunTrainer()
    if kind == "hf":
        from .hf import HFLoraTrainer
        return HFLoraTrainer()
    raise ValueError(kind)
