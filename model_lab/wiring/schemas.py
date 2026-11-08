"""영역 내부 스키마(리포 루트 `schemas/`)의 적재와 검증. 컴포넌트 사이의 스키마를 아는 것은 영역의 몫이다(D104 규칙 5).

컴포넌트는 입력을 `check(이름, 문서)` 하나로 검증한다(손 검사 금지, DESIGN 5절). 영역 사이 타입은 복사하지 않고 상대 참조(`{"$ref": "ServingConfig.schema.json"}` 처럼 계약 파일 이름 그대로)로 계약 패키지의 스키마를 가리킨다(DESIGN 3절). 이 파일은 그 참조를 설치된 계약 패키지로 풀어 준다."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import jsonschema
from boundary_contracts import schema_dir as _contract_dir
from boundary_contracts import format_checker
from boundary_contracts.schemas_api import ContractValidator

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"
_BASE = "https://openit.example/model-lab/schemas/"


@lru_cache(maxsize=1)
def _registry():
    """계약 registry(안정 URI·문진 스키마 위임 포함)에 영역 스키마와 계약 스키마를 이 영역의 기준 주소 아래에도 올린다.
    왜: 영역 스키마의 상대 `$ref`(`ServingConfig.schema.json` 같은 계약 파일 이름, 다른 영역 스키마)가 _BASE 기준으로 풀린다(DESIGN 3절)."""
    from referencing import Resource
    from referencing.jsonschema import DRAFT202012
    from boundary_contracts.schemas_api import schema_registry
    reg = schema_registry()
    own = {}
    for p in SCHEMAS_DIR.glob("*.schema.json"):
        own[p.name] = json.loads(p.read_text(encoding="utf-8"))
    for p in Path(_contract_dir()).glob("*.schema.json"):
        if p.name in own:
            raise RuntimeError(f"영역 스키마 {p.name}이 계약 스키마와 이름이 겹친다(영역 사이 타입은 복사하지 않는다, DESIGN 3절)")
        own[p.name] = json.loads(p.read_text(encoding="utf-8"))
    for name, doc in own.items():
        reg = reg.with_resource(_BASE + name, Resource.from_contents({**doc, "$id": _BASE + name}, default_specification=DRAFT202012))
    return reg, own


@lru_cache(maxsize=None)
def validator(name: str):
    reg, docs = _registry()
    # 왜 계약 검증기 부품을 그대로 쓰나: schema_validator 는 계약 registry 만 쓰므로 영역 스키마의 상대 $ref 를 못 푼다. 같은 규칙(형식 검사 강제·비유한 수 거부)을 영역 registry 로 건다
    schema = {**docs[f"{name}.schema.json"], "$id": _BASE + f"{name}.schema.json"}
    jsonschema.Draft202012Validator.check_schema(schema)
    return ContractValidator(jsonschema.Draft202012Validator(schema, registry=reg, format_checker=format_checker(schema)))


def check(name: str, doc) -> None:
    """doc이 스키마 `name`을 어기면 jsonschema.ValidationError(가장 얕은 위반)를 낸다."""
    errs = sorted(validator(name).iter_errors(doc), key=lambda e: (len(list(e.absolute_path)), list(map(str, e.absolute_path))))
    if errs:
        raise errs[0]


def errors(name: str, doc) -> list[jsonschema.ValidationError]:
    return sorted(validator(name).iter_errors(doc), key=lambda e: list(map(str, e.absolute_path)))


def describe(e: jsonschema.ValidationError) -> str:
    """위반 한 건의 설명: 위치와 위반한 규칙 이름만. 값은 싣지 않는다(jsonschema 의 e.message 는 값을 담는다, 계약 INTERFACES I7).
    모르는 키(additionalProperties)만 예외로 키 이름을 싣는다: 값이 아니라 설정 작성자가 쓴 이름이고, 오타를 찾는 데 필요하다."""
    where = "/" + "/".join(map(str, e.absolute_path))
    if e.validator == "additionalProperties" and isinstance(e.instance, dict):
        extra = sorted(set(e.instance) - set((e.schema.get("properties") or {})))
        return f"{where}: additionalProperties({', '.join(map(str, extra))})"
    return f"{where}: {e.validator}"
