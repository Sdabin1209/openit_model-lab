"""U49: 영역이 소유하는 형식 스키마(docs/schemas, 학습 서버와의 형식)와 계약 사본이 없음을 지키는 시험.
계약으로 승격된 스키마(ModelPackageLayout 등)의 사본은 두지 않는다. 계약 패키지(boundary_contracts)가 정본이다."""
import json
from pathlib import Path

import jsonschema
import pytest
from boundary_contracts import schema_dir

from tests.conftest import ROOT

SCHEMAS = ROOT / "docs" / "schemas"
OWNED = ["server-dataset-row", "server-train-invocation", "train-wrap-record"]


def load(name):
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))


def test_only_area_owned_schemas_remain_and_none_is_a_copy_of_a_contract_schema():
    assert sorted(p.name.removesuffix(".schema.json") for p in SCHEMAS.glob("*.schema.json")) == sorted(OWNED)
    contract = {p.name.removesuffix(".schema.json").lower() for p in Path(schema_dir()).glob("*.schema.json")}
    for name in OWNED:
        assert name.replace("-", "") not in {c.replace("-", "") for c in contract}, f"{name}은 계약에 있다: 사본을 지운다"
    assert not (ROOT / "docs" / "change-requests" / "schemas").exists() and not (ROOT / "docs" / "change-requests" / "contract-ready").exists()


@pytest.mark.parametrize("name", OWNED)
def test_examples_are_accepted_or_rejected_as_named(name):
    v = jsonschema.Draft202012Validator(load(name))
    for p in sorted((SCHEMAS / "examples").glob(f"{name}*.json")):
        ok = not list(v.iter_errors(json.loads(p.read_text(encoding="utf-8"))))
        assert ok == ("invalid" not in p.name), p.name


def test_real_server_rows_satisfy_server_dataset_row():
    v = jsonschema.Draft202012Validator(load("server-dataset-row"))
    n = 0
    for f in sorted((ROOT / "tests" / "data").glob("server_rows_lora_*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            assert not list(v.iter_errors(json.loads(line))), f.name
            n += 1
    assert n > 10
