"""학습 데이터 변환 컴포넌트: 계약 샘플 하나를 학습 예시 {sample_id, prompt, completion}로 바꾼다(순수 함수).

입력 TrainDataRequest {role, sample, serving_config, system_prompt}, 출력 TrainExample. prompt는 서빙과 같은 model-runtime render_prompt이고
completion은 서버 학습 데이터의 assistant 글자와 같다(D74).

주의(미해결): 서빙의 render_prompt는 입력 JSON을 JCS(공백 없음, 키 정렬)로 쓰고, 서버 학습 데이터의 user 글자는 공백이 있고 state 순서를
보존한다. 이 컴포넌트로 학습한 어댑터는 서빙과 같은 글자로 학습되지만, 서버에서 학습한 어댑터는 다르다(CR-L007 4번, CR-L008)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from ..wiring.errors import ConversionError

_ROLES = ("LORA_A", "LORA_B")


def to_example(role: str, sample: Mapping, serving_config: Mapping, system_prompt: str, schema_dir: Path) -> dict:
    """학습 예시 {sample_id, prompt, completion}. prompt는 서빙과 같은 render_prompt, completion은 서버 학습 데이터의 assistant 글자와 같다."""
    import model_runtime
    prompt = model_runtime.render_prompt(role, sample["input"], serving_config=serving_config, system_prompt=system_prompt,
                                         schema_dir=schema_dir)
    return {"sample_id": sample["sample_id"], "prompt": prompt, "completion": render_output(role, sample["expected"])}


def render_output(role: str, expected) -> str:
    """정답 -> assistant 글자. LoRA-A는 키를 정렬한 JSON, LoRA-B는 문장 그대로(JSON이 아니다)."""
    if role not in _ROLES:
        raise ConversionError(f"알 수 없는 역할: {role}")
    if role == "LORA_B":
        if not isinstance(expected, str):
            raise ConversionError("LoRA-B의 정답은 문장(문자열)이어야 한다")
        return expected
    return json.dumps(expected, ensure_ascii=False, sort_keys=True)
