"""회귀 평가 연결: 실패 사례 읽기(link_failure_read) -> 회귀 집합(regression_set) -> 채점·보고(evaluation)를 잇는다(wiring)."""
from __future__ import annotations

from ..components import link_failure_read, regression_set
from ..components.regression_set import TRACE_KEYS, kind_of  # noqa: F401
from .evaluation import evaluate_set, regression_section as section  # noqa: F401


def build_set(store, schema_dir=None, *, limit: int | None = None, max_listed: int = 20) -> dict:
    cases = link_failure_read.list_confirmed(store, limit=limit)
    return regression_set.build_set(cases, lambda ref: link_failure_read.get_trace(store, ref), max_listed=max_listed)
