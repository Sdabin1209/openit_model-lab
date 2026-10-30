"""어댑터 점검 컴포넌트: 어댑터 디렉터리의 필수 파일, adapter_config.json, 기반 모델 일치, 순위 상한을 확인한다.

입력 AdapterCheckRequest {role, adapter_dir, base_model{id}, max_rank}, 출력 AdapterConfig(adapter_config.json의 내용)."""
from __future__ import annotations

import json
from pathlib import Path

from ..wiring.errors import PipelineError

ADAPTER_FILES = ("adapter_model.safetensors", "adapter_config.json")


def check_adapter(role: str, adapter_dir: Path, base_model: dict, max_rank: int | None) -> dict:
    """어댑터 디렉터리의 필수 파일과 기반 모델·순위를 확인한다. adapter_config의 내용을 돌려준다."""
    adapter_dir = Path(adapter_dir)
    for f in ADAPTER_FILES:
        if not (adapter_dir / f).is_file():
            raise PipelineError(f"{role}: 어댑터 디렉터리에 {f}가 없다")
    try:
        conf = json.loads((adapter_dir / "adapter_config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PipelineError(f"{role}: adapter_config.json을 읽을 수 없다") from None
    if not isinstance(conf, dict):
        raise PipelineError(f"{role}: adapter_config.json의 최상위는 객체여야 한다")
    got = conf.get("base_model_name_or_path")
    if got != base_model["id"]:
        raise PipelineError(f"{role}: 어댑터의 base_model_name_or_path({got!r})가 프로파일의 기반 모델({base_model['id']!r})과 다르다. "
                            "다른 기반 모델로 학습된 어댑터는 얹을 수 없다")
    r = conf.get("r")
    if max_rank is not None and (not isinstance(r, int) or r > max_rank):
        raise PipelineError(f"{role}: 어댑터 순위 {r}가 대상 엔진의 max_lora_rank {max_rank}를 넘는다")
    return conf


def check_rank(role: str, adapter_dir: Path, max_rank: int) -> None:
    """어댑터 순위 r이 대상 엔진 프로파일의 상한(max_lora_rank, D48)을 넘으면 중단한다. 게시 전 확인."""
    r = json.loads((Path(adapter_dir) / "adapter_config.json").read_text()).get("r")
    if r is None or r > max_rank:
        raise PipelineError(f"{role}: 어댑터 순위 {r}가 대상 엔진의 max_lora_rank {max_rank}를 넘는다")
