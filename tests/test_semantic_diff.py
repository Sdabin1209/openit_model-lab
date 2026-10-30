"""D109: turn-engine 코드 의존을 계약 check_action_extraction + 문진 스키마 흐름표로 옮겨도 평가 지표 값이 바뀌지 않는다.
기대값(tests/data/semantic_violations_before.json)은 이전 구현(turn-engine v0.4.3 validate_extraction, DATASET_V2_FLOW)이 같은 입력에 낸 코드다.
이전 구현을 다시 부르지 않고 기록으로 비교하는 이유: turn-engine이 의존에서 빠져 시험 환경에 없다."""
import json
from pathlib import Path

from boundary_contracts import validator

from model_lab.components.eval_sample import semantic_violations

DATA = json.loads((Path(__file__).parent / "data" / "semantic_violations_before.json").read_text(encoding="utf-8"))


def test_record_covers_every_code_and_the_uncheckable_case():
    seen = {c for case in DATA["cases"] if case["codes"] for c in case["codes"]}
    assert {"SAVE_RULE", "UNKNOWN_ITEM", "TARGET_NOT_IN_VALUES", "NEXT_ITEM_MISMATCH", "SKIP_ORDER_MISMATCH", "OUTPUT_SCHEMA"} <= seen
    assert any(case["codes"] is None for case in DATA["cases"]) and any(case["codes"] == [] for case in DATA["cases"])


def test_same_codes_as_before_for_every_schema_valid_output():
    """평가기는 raw.valid(ActionExtraction 스키마 통과) 출력만 넘긴다. 그 범위에서는 이전 구현과 위반 코드가 모두 같아야 한다."""
    valid = validator("ActionExtraction")
    checked = [(i, c) for i, c in enumerate(DATA["cases"]) if isinstance(c["output"], dict) and valid.is_valid(c["output"])]
    assert len(checked) >= 200
    diffs = [(i, c["codes"], semantic_violations(c["input"], c["output"])) for i, c in checked
             if semantic_violations(c["input"], c["output"]) != c["codes"]]
    assert not diffs, f"{len(diffs)}건 다름(번호, 이전, 지금): {diffs[:5]}"


def test_schema_invalid_outputs_differ_only_by_output_schema_code():
    """스키마를 어긴 출력은 이전 구현이 OUTPUT_SCHEMA를 더 냈다. 형식 검사는 model-runtime이 보증하므로(R7) 여기서 다시 하지 않는다.
    그 밖의 코드와 '검사할 수 없음(None)'은 같아야 한다."""
    valid = validator("ActionExtraction")
    drop = lambda codes: None if codes is None else sorted(set(codes) - {"OUTPUT_SCHEMA"})
    diffs = [(i, c["codes"]) for i, c in enumerate(DATA["cases"])
             if not (isinstance(c["output"], dict) and valid.is_valid(c["output"]))
             and drop(c["codes"]) != drop(semantic_violations(c["input"], c["output"]))]
    assert not diffs
