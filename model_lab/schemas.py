"""문진 스키마(survey-service/schema/schemas) 읽기. 경로(schema_dir)로 받고 복사하지 않는다."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import jsonschema

from .errors import PipelineError


def contract_version(schema_dir: Path) -> int:
    p = Path(schema_dir).parent / "CONTRACT_VERSION"
    try:
        return int(p.read_text().strip())
    except (OSError, ValueError):
        raise PipelineError("schema_dir 상위의 CONTRACT_VERSION을 읽을 수 없다") from None


@lru_cache(maxsize=None)
def _validator(schema_dir: str, name: str):
    # model-runtime과 같이 상대 $id를 뺀다(그대로면 '#/$defs/..' 참조가 원격 주소로 풀리려 한다).
    schema = json.loads((Path(schema_dir) / f"{name}.json").read_text(encoding="utf-8"))
    schema.pop("$id", None)
    return jsonschema.Draft202012Validator(schema)


def survey_validator(schema_dir: Path, name: str):
    return _validator(str(Path(schema_dir).resolve()), name)
