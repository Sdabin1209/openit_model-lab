"""codex C2: 설정 오류 설명에 입력의 키·값이 실리지 않는다(INTERFACES I7). 빠진 키 이름(스키마 이름)은 싣는다."""
import jsonschema
from model_lab.wiring.schemas import describe

CANARY = "CANARY-KEY-91c2"


def test_unknown_key_and_value_not_echoed():
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "additionalProperties": False, "required": ["a"]}
    errs = list(jsonschema.Draft202012Validator(schema).iter_errors({CANARY: CANARY, "a": CANARY}))
    text = " ".join(describe(e) for e in errs)
    assert CANARY not in text
    assert "additionalProperties" in text and "type" in text


def test_missing_required_name_is_kept():
    schema = {"type": "object", "required": ["dataset"]}
    (e,) = list(jsonschema.Draft202012Validator(schema).iter_errors({}))
    assert "required(dataset)" in describe(e)
