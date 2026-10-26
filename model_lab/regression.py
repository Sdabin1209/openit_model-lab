"""회귀 평가(I7). 확정된(CONFIRMED) 실패 사례 중 정답(expected)이 있는 것을 회귀 집합으로 만든다.

2.0.0(D74): 종류는 `expected_kind`(LORA_A / LORA_B). 없으면 expected의 형태로 판별한다(객체 = LORA_A, 문자열 = LORA_B).
입력은 `reviewed_input`(검토자가 고친 입력)을 우선하고(D55), 없으면 비식별 trace에서 읽는다. 입력은 LORA_A면 ActionExtractionInput,
LORA_B면 ResponseRequest로, 정답은 ActionExtraction / ResponseText로 검증한다. 통과하지 못하면 건너뛰고 사유를 센다.
임계치는 정하지 않는다. 일치 수 등 계산한 값만 보고서에 담는다(O11).

[미정] 비식별 trace 안의 모델 입력 키 이름. v1은 parser_input/generator_input이었고 v2의 키는 survey-service가 정한다(지시서: extract_* 등).
아래 _TRACE_KEYS는 추정이며 확정되면 바꾼다. 맞는 키가 없으면 trace_input_missing으로 건너뛰므로 reviewed_input 사례는 영향이 없다."""
from __future__ import annotations

import collections
from pathlib import Path

from boundary_contracts import BoundaryError, validator

from .evaluate import aggregate, evaluate_sample

KIND_ROLE = {"LORA_A": "LORA_A", "LORA_B": "LORA_B"}
_IN = {"LORA_A": "ActionExtractionInput", "LORA_B": "ResponseRequest"}
_OUT = {"LORA_A": "ActionExtraction", "LORA_B": "ResponseText"}
_TRACE_KEYS = {"LORA_A": ("extract_input",), "LORA_B": ("response_input", "respond_input")}   # 추정(미정)


def kind_of(case: dict) -> str | None:
    k = case.get("expected_kind")
    if k in KIND_ROLE:
        return k
    e = case.get("expected")
    if isinstance(e, dict):
        return "LORA_A"
    if isinstance(e, str):
        return "LORA_B"
    return None     # 모르는 값은 알 수 없음


def list_confirmed(store, *, limit: int | None = None) -> list[dict]:
    out, cur = [], None
    while True:
        items, cur = store.list_cases(review_status="CONFIRMED", latest_only=True, cursor=cur, limit=100)
        out += items
        if not cur or (limit and len(out) >= limit):
            break
    out = [c for c in out if c.get("expected") is not None]
    return out[:limit] if limit else out


def build_set(store, schema_dir: Path | None, *, limit: int | None = None, max_listed: int = 20) -> dict:
    """{"samples": {role: [...]}, "skipped": {reason: {"count", "case_ids"}}, "cases_total": n}"""
    cases = list_confirmed(store, limit=limit)
    samples: dict[str, list] = {"LORA_A": [], "LORA_B": []}
    skipped: dict[str, list] = collections.defaultdict(list)
    for c in cases:
        cid = f"{c['case_id']}@{c['version']}"
        kind = kind_of(c)
        if kind is None:
            skipped["kind_unknown"].append(cid); continue
        role = KIND_ROLE[kind]
        src = "reviewed_input"
        inp = c.get("reviewed_input")      # 검토자가 개인정보를 뺀 문장으로 고쳐 쓴 입력을 우선한다(D55)
        if not isinstance(inp, dict):
            src = "trace"
            try:
                trace = store.get_trace(c["deidentified_trace_ref"])
            except BoundaryError as e:
                if e.code == "NOT_FOUND":
                    skipped["trace_not_found"].append(cid); continue
                raise                  # UNAVAILABLE 등은 중단(재시도)
            inp = next((trace[k] for k in _TRACE_KEYS[role] if isinstance(trace, dict) and isinstance(trace.get(k), dict)), None)
        if not isinstance(inp, dict):
            skipped["trace_input_missing"].append(cid); continue
        if list(validator(_IN[role]).iter_errors(inp)):
            skipped["input_invalid"].append(cid); continue
        if list(validator(_OUT[role]).iter_errors(c["expected"])):
            skipped["expected_invalid"].append(cid); continue
        samples[role].append({"sample_id": cid, "input": inp, "expected": c["expected"], "failure_type": c["failure_type"],
                              "source_release_id": c["source_release_id"], "input_source": src})
    return {"samples": samples, "cases_total": len(cases),
            "skipped": {k: {"count": len(v), "case_ids": v[:max_listed]} for k, v in sorted(skipped.items())}}


def evaluate_set(role: str, session, rset: dict) -> list:
    return [(s, evaluate_sample(role, session, s)) for s in rset["samples"][role]]


def section(role: str, session, rset: dict, *, max_listed: int = 20, recs: list | None = None) -> dict:
    """보고서의 `regression` 절."""
    ss = rset["samples"][role]
    recs = recs if recs is not None else evaluate_set(role, session, rset)
    by_type: dict = collections.defaultdict(list)
    for s, r in recs:
        by_type[s["failure_type"]].append(r)
    fails = [{"sample_id": s["sample_id"], "failure_type": s["failure_type"], "raw_valid": r["raw_valid"], "outcome": r["outcome"]}
             for s, r in recs if not r["prot_exact"]]
    return {"enabled": True, "cases_total": rset["cases_total"], "used_for_role": len(ss), "input_sources": dict(collections.Counter(s["input_source"] for s in ss)), "skipped": rset["skipped"],
            "aggregate": aggregate([r for _, r in recs], role),
            "by_failure_type": {k: aggregate(v, role) for k, v in sorted(by_type.items())},
            "not_matching": {"count": len(fails), "listed": fails[:max_listed]},
            "note": "확정된 실패 사례(정답 있음)의 비식별 입력으로 평가한 값이다. 임계치는 정해지지 않았다(O11)."}
