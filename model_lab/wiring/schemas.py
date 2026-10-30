"""영역 내부 스키마(리포 루트 `schemas/`)의 적재와 검증. 컴포넌트 사이의 스키마를 아는 것은 영역의 몫이다(D104 규칙 5).

컴포넌트는 입력을 `check(이름, 문서)` 하나로 검증한다(손 검사 금지, DESIGN 5절). 영역 사이 타입은 복사하지 않고 상대 참조(`{"$ref": "ServingConfig.schema.json"}` 처럼 계약 파일 이름 그대로)로 계약 패키지의 스키마를 가리킨다(DESIGN 3절). 이 파일은 그 참조를 설치된 계약 패키지로 풀어 준다."""
from __future__ import annotations

import json
import warnings
from functools import lru_cache
from pathlib import Path

import jsonschema
from boundary_contracts import schema_dir as _contract_dir

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"
_BASE = "https://openit.example/model-lab/schemas/"


@lru_cache(maxsize=1)
def _store() -> dict:
    store = {}
    for p in Path(_contract_dir()).glob("*.schema.json"):
        store[_BASE + p.name] = json.loads(p.read_text(encoding="utf-8"))
    for p in SCHEMAS_DIR.glob("*.schema.json"):
        if _BASE + p.name in store:
            raise RuntimeError(f"영역 스키마 {p.name}이 계약 스키마와 이름이 겹친다(영역 사이 타입은 복사하지 않는다, DESIGN 3절)")
        store[_BASE + p.name] = json.loads(p.read_text(encoding="utf-8"))
    return store


@lru_cache(maxsize=None)
def validator(name: str) -> jsonschema.Draft202012Validator:
    schema = _store()[_BASE + f"{name}.schema.json"]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)   # 계약 패키지의 validator()와 같은 RefResolver 방식
        return jsonschema.Draft202012Validator(schema, resolver=jsonschema.RefResolver(_BASE + f"{name}.schema.json", schema, store=_store()))


def check(name: str, doc) -> None:
    """doc이 스키마 `name`을 어기면 jsonschema.ValidationError(가장 얕은 위반)를 낸다."""
    errs = sorted(validator(name).iter_errors(doc), key=lambda e: (len(list(e.absolute_path)), list(map(str, e.absolute_path))))
    if errs:
        raise errs[0]


def errors(name: str, doc) -> list[jsonschema.ValidationError]:
    return sorted(validator(name).iter_errors(doc), key=lambda e: list(map(str, e.absolute_path)))
