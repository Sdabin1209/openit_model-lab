"""EvaluationReport(boundary-contracts v1.3.0)의 지표를 METRICS.md(제안) 이름으로 계산한다.

합격 판정·임계치는 없다. 계산하지 못하는 지표는 n=0으로 두지 않고 생략하고 notes에 이유를 적는다.
슬라이스: ALL, SEEN_UNSEEN, QUESTION_SET, QUESTION_TYPE(슬롯 유형), UNIT, RANGE, INTENT, NOISE."""
from __future__ import annotations

from typing import Callable, Mapping

from boundary_contracts import PIPELINE_SCHEMA_VERSION, canonical, validator

from .datasets import qs_key
from .evaluate import sample_dims

SLICE_KIND = {"seen": "SEEN_UNSEEN", "question_set": "QUESTION_SET", "slot_kind": "QUESTION_TYPE", "unit": "UNIT",
              "range": "RANGE", "intent": "INTENT", "noise_type": "NOISE", "action": "ACTION", "question_id": "QUESTION"}
SLICE_DIMS = {"LORA_A": ("seen", "question_set", "slot_kind", "unit", "range", "intent", "noise_type"),
              "LORA_B": ("seen", "question_set", "action")}


def _m(name, group, value, n, hib, unit="ratio", slice_=None) -> dict:
    d = {"name": name, "group": group, "value": float(value), "n": int(n), "higher_is_better": hib, "unit": unit}
    if slice_:
        d["slice"] = {"kind": slice_[0], "value": slice_[1]}
    return d


def _ratio(recs, num: Callable[[dict], bool], den: Callable[[dict], bool]):
    d = [r for r in recs if den(r)]
    return (sum(1 for r in d if num(r)) / len(d), len(d)) if d else None


def metrics_for(role: str, recs: list[dict], slice_=None) -> list[dict]:
    out: list[dict] = []

    def add(name, group, res, hib, unit="ratio"):
        if res is not None:
            out.append(_m(name, group, res[0], res[1], hib, unit, slice_))

    if role == "LORA_A":
        add("risk_signal_recall", "SAFETY", _ratio(recs, lambda r: r["out_risk"], lambda r: r["exp_risk"]), True)
        add("risk_signal_false_alarm", "SAFETY", _ratio(recs, lambda r: r["out_risk"], lambda r: not r["exp_risk"]), False)
        tp, fp, fn = (sum(r[f"prot_item_{k}"] for r in recs) for k in ("tp", "fp", "fn"))
        if tp + fp:
            out.append(_m("item_precision", "ACCURACY", tp / (tp + fp), tp + fp, True, slice_=slice_))
        if tp + fn:
            out.append(_m("item_recall", "ACCURACY", tp / (tp + fn), tp + fn, True, slice_=slice_))
        for name, h, t in (("target_accuracy", "target_hit", "target_total"), ("value_exact_match", "value_hit", "value_total"),
                           ("intent_accuracy", "intent_hit", "intent_total")):
            tot = sum(r[t] for r in recs)
            if tot:
                out.append(_m(name, "ACCURACY", sum(r[h] for r in recs) / tot, tot, True, slice_=slice_))
        add("fallback_rate", "PROTECTION", _ratio(recs, lambda r: r["outcome"] == "fallback", lambda r: True), False)
    else:
        add("risk_notice_verbatim_rate", "SAFETY", _ratio(recs, lambda r: r["risk_notice_verbatim"], lambda r: "risk_notice_total" in r), True)
        add("ungrounded_number_rate", "SAFETY", _ratio(recs, lambda r: r["ungrounded"], lambda r: "ungrounded_checked" in r), False)
        add("slot_coverage", "ACCURACY", _ratio(recs, lambda r: r["slot_covered"], lambda r: "slot_total" in r), True)
        add("fallback_rate", "PROTECTION", _ratio(recs, lambda r: r["is_fallback"], lambda r: True), False)
    add("schema_valid_raw", "PROTECTION", _ratio(recs, lambda r: r["raw_valid"], lambda r: True), True)
    iv = _ratio(recs, lambda r: r["outcome"] in ("repaired", "fallback"), lambda r: True)
    if iv:
        out.append(_m("protection_intervened", "PROTECTION", iv[0] * iv[1], iv[1], False, "count", slice_))
    return out


def build_metrics(role: str, pairs: list[tuple[dict, dict]], seen_sets: set, *, regression_recs: list[dict] | None = None):
    """pairs = [(sample, rec)]. 지표 목록과 notes를 낸다."""
    recs = [r for _, r in pairs]
    metrics = metrics_for(role, recs)
    buckets: dict = {}
    for s, r in pairs:
        for dim, vals in sample_dims(role, s, seen_sets).items():
            if dim in SLICE_DIMS[role]:
                for v in vals:
                    buckets.setdefault((SLICE_KIND[dim], v), []).append(r)
    for key in sorted(buckets):
        metrics += metrics_for(role, buckets[key], key)
    seen = _ratio([r for s, r in pairs if qs_key(s["question_set"]) in seen_sets], lambda r: r["prot_exact"], lambda r: True)
    unseen = _ratio([r for s, r in pairs if qs_key(s["question_set"]) not in seen_sets], lambda r: r["prot_exact"], lambda r: True)
    if seen and unseen:
        metrics.append(_m("coverage_seen_unseen_gap", "COVERAGE", seen[0] - unseen[0], seen[1] + unseen[1], False, "score"))
    if regression_recs:
        n = len(regression_recs)
        ok = sum(1 for r in regression_recs if r["prot_exact"])
        metrics.append(_m("regression_fixed", "REGRESSION", ok, n, True, "count"))
        metrics.append(_m("regression_broken", "REGRESSION", n - ok, n, False, "count"))
    notes = [
        "합격 판정과 임계치는 없다. 값은 계산한 그대로이며 평가 백엔드는 서빙과 다르고 제약 디코딩을 쓰지 않았다.",
        "정확도 지표(item_*, target_accuracy, value_exact_match, intent_accuracy, slot_coverage 등)와 fallback_rate는 보호 적용 후(서빙되는) 출력 기준이고, schema_valid_raw와 ungrounded_number_rate는 보호 전 모델 출력 기준이다.",
        "value_exact_match는 문자열 완전 일치다(단위 환산 전 값 비교, 환산은 정규화 단계의 일).",
        "item_precision의 n은 출력 항목 수, item_recall의 n은 정답 항목 수다.",
        "coverage_seen_unseen_gap은 (본 문항 정답 일치율 - 보지 못한 문항 정답 일치율)이며 둘 다 표본이 있을 때만 낸다. 일치는 정답 출력과의 완전 일치다.",
    ]
    if role == "LORA_B":
        notes += ["forbidden_expression_rate는 생략했다: 금지 표현 목록이 정해지지 않았다(model-runtime도 만들지 않음, 규칙 2·3은 기계로 확인하지 못한다).",
                  "length_p95_ms는 생략했다: 이 평가는 응답 지연을 측정하지 않는다.",
                  "ungrounded_number_rate는 아라비아 숫자 열만 비교한다(한글 수사는 확인하지 못한다). slot_coverage는 say_values·notices의 값 문자열이 응답에 그대로 들어 있는지로 본다."]
    else:
        notes += ["risk_signal_*는 정답 항목에 RISK_SIGNAL 의도가 있는지로 샘플을 나눈다."]
    if not regression_recs:
        notes.append("regression_fixed/regression_broken은 생략했다: 회귀 평가를 켜지 않았거나 이 역할의 회귀 사례가 없다.")
    present = {m["name"] for m in metrics}
    for name in ("risk_signal_recall", "risk_notice_verbatim_rate", "slot_coverage", "ungrounded_number_rate", "value_exact_match"):
        if name not in present and ((name.startswith("risk_signal") and role == "LORA_A") or (role == "LORA_B" and name in ("risk_notice_verbatim_rate", "slot_coverage", "ungrounded_number_rate")) or name == "value_exact_match" and role == "LORA_A"):
            notes.append(f"{name}은 생략했다: 이 평가 데이터에 해당 표본이 없다.")
    return metrics, notes


def evaluation_report(*, role: str, report_id: str, created_at: str, package: Mapping, dataset: Mapping, question_sets: list,
                      backend: Mapping, decoding: Mapping, metrics: list, notes: list, detail: Mapping) -> dict:
    r = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "report_id": report_id, "created_at": created_at, "role": role,
         "package": dict(package), "dataset": dict(dataset), "question_sets": list(question_sets),
         "evaluation_backend": {"name": backend["name"], "version": backend["version"],
                                "constrained_decoding": bool(backend.get("constrained_decoding", False))},
         "decoding": {k: decoding[k] for k in ("temperature", "seed") if k in decoding},
         "metrics": metrics, "notes": notes, "ext": {"model-lab": {"detail": detail}}}
    r["content_hash"] = canonical.content_hash(r)
    validator("EvaluationReport").validate(r)
    return r
