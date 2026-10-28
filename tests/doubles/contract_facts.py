"""계약 패키지의 스키마에서 읽는 사실. 시험 대상 코드가 쓰는 상수(PIPELINE_SCHEMA_VERSION)와 독립인 출처라서,
'코드가 낸 값 == 코드의 상수' 같은 항상 참인 비교를 스키마 형식 검사로 보강한다."""
import json
import re
from importlib import resources


def pipeline_version_pattern() -> re.Pattern:
    d = json.loads(resources.files("boundary_contracts").joinpath("schemas/_common.schema.json").read_text(encoding="utf-8"))
    return re.compile(d["$defs"]["PipelineSchemaVersion"]["pattern"])
