"""학습 데이터 변환. 프롬프트는 model-runtime의 render_prompt(서빙과 같은 함수)로 만든다.

모델은 서빙 출력 계약대로만 낸다. 파서는 ParserOutput 전체, 생성기는 {"response_text"}뿐이다.
is_fallback은 코드가 채우므로 학습 목표에 넣지 않는다(D36)."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

import model_runtime
from boundary_contracts import canonical


def completion_of(role: str, expected: Mapping) -> str:
    """정답 출력 -> 모델이 내야 하는 문자열(결정적인 JCS JSON)."""
    if role == "LORA_B":
        obj = {"response_text": expected["response_text"]}
    else:
        obj = dict(expected)
    return canonical.canonicalize(obj).decode("utf-8")


def to_example(role: str, sample: Mapping, serving_config: Mapping, schema_dir: Path) -> dict:
    prompt = model_runtime.render_prompt(role, sample["input"], serving_config=serving_config, schema_dir=schema_dir)
    return {"sample_id": sample["sample_id"], "prompt": prompt, "completion": completion_of(role, sample["expected"])}
