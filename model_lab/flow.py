"""LoRA-A 출력 규칙 검사에 쓰는 문항 순서·건너뛰기 표.

왜 문진 스키마에서 읽나: 흐름표의 정본은 문진 스키마 `question_flow`(survey-service 소유)이고 사본을 두지 않는다(D109). 계약
`QuestionFlowTable`이 요구하는 모양은 order와 skips뿐이라 나머지 필드(설명, 근거, slot_order)는 떼어 낸다.
이 표는 학습 데이터셋 v2가 쓰는 순서·건너뛰기이며 서비스 동작의 근거가 아니다(D99)."""
from __future__ import annotations

from functools import lru_cache

import survey_schema
from boundary_contracts import validator
from boundary_contracts.action_rules import load_rules


@lru_cache(maxsize=1)
def default_flow() -> dict:
    data = survey_schema.load_data("question_flow")
    flow = {"order": list(data["order"]), "skips": [dict(s) for s in data["skips"]]}
    validator("QuestionFlowTable").validate(flow)
    return flow


def violation_codes() -> tuple[str, ...]:
    """semantic_violation_rate가 코드별로 내는 위반 코드. 정본은 계약 action_extraction_rules.json이다(코드 이름을 여기에 베끼지 않는다).
    OUTPUT_SCHEMA는 check_action_extraction이 출력 구조가 ActionExtraction이 아닐 때 내는 코드다. 순서는 이전 구현(turn-engine CODES)과 같게 둔다
    (보고서의 지표 순서가 바뀌지 않게)."""
    r = load_rules()
    return ("OUTPUT_SCHEMA", r["targets"]["code"], r["next_item"]["mismatch_code"], r["next_item"]["skip_order_code"],
            r["save_rule"]["code"], r["unknown_item"]["code"])
