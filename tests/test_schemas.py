"""D117 6절 4항: schemas/의 모든 스키마에 통과·거부 예시가 있고 결과가 맞다. 영역 스키마는 계약 스키마의 사본이 아니다(이름이 겹치지 않는다).
스키마 파일 이름 = 타입 이름, 예시는 schemas/examples/<타입>.valid*.json, <타입>.invalid*.json."""
import json
from pathlib import Path

import jsonschema
import pytest
from boundary_contracts import schema_dir

from model_lab.wiring import schemas as S
from tests.conftest import ROOT

SCHEMAS = ROOT / "schemas"
NAMES = sorted(p.name.removesuffix(".schema.json") for p in SCHEMAS.glob("*.schema.json"))


def test_no_schema_is_a_copy_of_a_contract_schema():
    contract = {p.name for p in Path(schema_dir()).glob("*.schema.json")}
    assert not [n for n in NAMES if f"{n}.schema.json" in contract]


@pytest.mark.parametrize("name", NAMES)
def test_schema_is_valid_and_examples_are_accepted_or_rejected_as_named(name):
    jsonschema.Draft202012Validator.check_schema(json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8")))
    ex = sorted((SCHEMAS / "examples").glob(f"{name}.*.json")) + sorted((SCHEMAS / "examples").glob(f"{name}.valid*.json"))
    ex = sorted(set(ex))
    assert any(".valid" in p.name for p in ex) and any(".invalid" in p.name for p in ex), f"{name}: 통과·거부 예시가 모두 있어야 한다"
    for p in ex:
        ok = not S.errors(name, json.loads(p.read_text(encoding="utf-8")))
        assert ok == (".invalid" not in p.name), p.name


def test_real_server_rows_satisfy_server_dataset_row():
    n = 0
    for f in sorted((ROOT / "tests" / "data").glob("server_rows_lora_*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            assert not S.errors("ServerDatasetRow", json.loads(line)), f.name
            n += 1
    assert n > 10
