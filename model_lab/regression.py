"""회귀 평가(I7). 확정된(CONFIRMED) 실패 사례 중 정답(expected)이 있는 것을 회귀 집합으로 만든다.

- 비식별된 것만 쓴다(FailureCase.deidentified는 스키마가 true로 고정, 클라이언트가 검증). trace는 `get_trace`의 비식별 trace이고
  구조 정보만 쓴다: 입력은 parser_input/generator_input이다. 없거나 입력 계약을 통과하지 못하면 건너뛰고 사유를 센다.
- 종류(`expected_kind`)가 없으면 expected의 형태로 판별한다(`items` 목록 = 파서, `response_text` = 생성기).
- 임계치는 정하지 않는다. 일치 수 등 계산한 값만 보고서에 담는다(O11).
"""
from __future__ import annotations

import collections
from pathlib import Path

from boundary_contracts import BoundaryError

from .evaluate import aggregate, evaluate_sample
from .schemas import survey_validator

KIND_ROLE = {"PARSER": "LORA_A", "GENERATOR": "LORA_B"}
_IN = {"LORA_A": "UtteranceParserInput", "LORA_B": "ResponseGeneratorInput"}
_OUT = {"LORA_A": "ParserOutput", "LORA_B": "GeneratorOutput"}
_TRACE_KEY = {"LORA_A": "parser_input", "LORA_B": "generator_input"}


def kind_of(case: dict) -> str | None:
    k = case.get("expected_kind")
    if k in KIND_ROLE:
        return k
    e = case.get("expected")
    if isinstance(e, dict):
        if isinstance(e.get("items"), list):
            return "PARSER"
        if "response_text" in e:
            return "GENERATOR"
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


def build_set(store, schema_dir: Path, *, limit: int | None = None, max_listed: int = 20) -> dict:
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
            inp = trace.get(_TRACE_KEY[role]) if isinstance(trace, dict) else None
        if not isinstance(inp, dict):
            skipped["trace_input_missing"].append(cid); continue
        if list(survey_validator(schema_dir, _IN[role]).iter_errors(inp)):
            skipped["input_invalid"].append(cid); continue
        if list(survey_validator(schema_dir, _OUT[role]).iter_errors(c["expected"])):
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
