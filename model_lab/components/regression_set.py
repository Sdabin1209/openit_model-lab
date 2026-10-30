"""회귀 집합 컴포넌트(I7의 사례를 평가 샘플로 바꾼다). 확정된(CONFIRMED) 실패 사례 중 정답(expected)이 있는 것을 회귀 집합으로 만든다.

2.0.0(D74): 종류는 `expected_kind`(LORA_A / LORA_B). 없으면 expected의 형태로 판별한다(객체 = LORA_A, 문자열 = LORA_B).
입력은 `reviewed_input`(검토자가 고친 입력)을 우선하고(D55), 없으면 비식별 trace에서 읽는다. 입력은 LORA_A면 ActionExtractionInput,
LORA_B면 ResponseRequest로, 정답은 ActionExtraction / ResponseText로 검증한다. 통과하지 못하면 건너뛰고 사유를 센다.
임계치는 정하지 않는다. 일치 수 등 계산한 값만 보고서에 담는다(O11).

비식별 trace 안의 모델 입력 키(D98 확정): 문진 스키마 TurnTrace의 `extract_input`(A 입력), `extract_output`, `response_input`(B 입력), `response_output`.
왜 이 이름: 문진 스키마가 정본이고 소비자는 스키마를 따른다(D21). 입력만 읽는다 — 출력 키는 '운영에서 낸 출력'이라 기대값(검토자가 확정)과 다르다.
trace 전체는 계약 OpsFeedbackDeidentifiedTrace(ops-feedback 소유, 모르는 키 거부)로 검증하고 어기면 trace_invalid로 건너뛴다(D107).
그 안의 모델 입력은 ActionExtractionInput / ResponseRequest로 따로 검증한다(trace 스키마는 입력의 모양을 느슨하게만 본다). 맞지 않으면 input_invalid,
맞는 키가 없으면 trace_input_missing."""
from __future__ import annotations

import collections

from boundary_contracts import BoundaryError, validator


KIND_ROLE = {"LORA_A": "LORA_A", "LORA_B": "LORA_B"}
_IN = {"LORA_A": "ActionExtractionInput", "LORA_B": "ResponseRequest"}
_OUT = {"LORA_A": "ActionExtraction", "LORA_B": "ResponseText"}
TRACE_KEYS = {"LORA_A": {"input": "extract_input", "output": "extract_output"},
              "LORA_B": {"input": "response_input", "output": "response_output"}}


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


def build_set(cases: list[dict], get_trace, *, max_listed: int = 20) -> dict:
    """RegressionSetRequest {cases(FailureCase 계약 타입, 정답 있는 확정 사례), max_listed} + 주입된 get_trace(trace_ref) -> trace
    -> RegressionSet {"samples": {role: [...]}, "skipped": {reason: {"count", "case_ids"}}, "cases_total": n}"""
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
                trace = get_trace(c["deidentified_trace_ref"])
            except BoundaryError as e:
                if e.code == "NOT_FOUND":
                    skipped["trace_not_found"].append(cid); continue
                raise                  # UNAVAILABLE 등은 중단(재시도)
            if not validator("OpsFeedbackDeidentifiedTrace").is_valid(trace):
                skipped["trace_invalid"].append(cid); continue      # I7 get_trace의 정본 스키마(모르는 키 거부). 어기면 평가 입력으로 쓰지 않는다
            inp = trace.get(TRACE_KEYS[role]["input"])
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
