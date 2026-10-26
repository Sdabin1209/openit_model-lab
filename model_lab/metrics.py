"""EvaluationReport(boundary-contracts v2.0.0)의 지표를 계산한다. 보고서 스키마는 계약 그대로다.

이름: LoRA-A는 학습 서버 score_a_v2.py의 지표 이름, LoRA-B는 METRICS.md(제안)의 `forbidden_expression_rate`·`ungrounded_number_rate`와
학습 서버 eval_lora_b_v2.py 항목(끝맺음, 다음 질문, 새 숫자, 금지 표현, 문장 수)을 따른 이름이다. METRICS.md는 v1 출력을 가정한 채로 아직
고쳐지지 않았다(계약 V2_DRAFT.md). 지표 이름은 자유 문자열이라 계약을 깨지 않는다.
합격 판정·임계치는 없다. 계산하지 못하는 지표는 n=0으로 두지 않고 생략하고 notes에 이유를 적는다.
슬라이스: ALL, SEEN_UNSEEN, QUESTION_SET, ACTION, REASON, NEXT_TYPE(B)."""
from __future__ import annotations

from typing import Callable, Mapping

from boundary_contracts import PIPELINE_SCHEMA_VERSION, canonical, validator

from .datasets import qs_key
from .evaluate import _sum_c, a_rates, sample_dims, sample_qs

SLICE_KIND = {"seen": "SEEN_UNSEEN", "question_set": "QUESTION_SET", "action": "ACTION", "reason": "REASON", "question_id": "QUESTION",
              "next_type": "NEXT_TYPE"}
SLICE_DIMS = {"LORA_A": ("seen", "question_set", "action", "reason"),
              "LORA_B": ("seen", "question_set", "action", "reason", "next_type")}
# LoRA-A 지표 이름은 학습 서버 score_a_v2.py와 같다. (이름, 그룹, 높을수록 좋은가)
A_RATE_METRICS = (("action_acc", "ACCURACY", True), ("action_reason_acc", "ACCURACY", True), ("next_item_acc", "ACCURACY", True),
                  ("key_found_rate", "ACCURACY", True), ("cell_acc", "ACCURACY", True), ("value_acc", "ACCURACY", True),
                  ("status_acc", "ACCURACY", True), ("save_acc", "ACCURACY", True), ("precision_acc", "ACCURACY", True),
                  ("db_row_match_rate", "ACCURACY", True), ("targets_acc", "ACCURACY", True), ("note_presence_acc", "ACCURACY", True),
                  ("exact_match_rate", "ACCURACY", True), ("oos_precision", "ACCURACY", True), ("oos_recall", "ACCURACY", True),
                  ("oos_f1", "ACCURACY", True))
# 분모: 행 수(rows) / 정답 칸 수(gold_keys) / targets가 있는 정답 행 / OOS 예측·정답 수
A_DENOM = {"key_found_rate": "gold_keys", "cell_acc": "gold_keys", "value_acc": "gold_keys", "status_acc": "gold_keys", "save_acc": "gold_keys",
           "precision_acc": "gold_keys", "targets_acc": "targets_rows"}


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
        t = _sum_c(recs)
        rates = a_rates(t)
        for name, group, hib in A_RATE_METRICS:
            if rates[name] is None:
                continue
            n = t.get(A_DENOM.get(name, "rows"), 0)
            if name.startswith("oos_"):
                n = t.get("oos_tp", 0) + (t.get("oos_fp", 0) if name == "oos_precision" else t.get("oos_fn", 0))
                if name == "oos_f1":
                    n = t.get("oos_tp", 0) + t.get("oos_fp", 0) + t.get("oos_fn", 0)
            if n:
                out.append(_m(name, group, rates[name], n, hib, slice_=slice_))
        for name in ("extra_keys", "dup_keys", "targets_extra_rows"):
            out.append(_m(name, "ACCURACY", t.get(name, 0), len(recs), False, "count", slice_))
        add("schema_ok_rate", "PROTECTION", _ratio(recs, lambda r: r["raw_valid"], lambda r: True), True)
        out.append(_m("output_invalid", "PROTECTION", sum(1 for r in recs if r["outcome"] == "invalid"), len(recs), False, "count", slice_))
    else:
        add("ends_ok_rate", "ACCURACY", _ratio(recs, lambda r: r["rules"]["ends_ok"], lambda r: True), True)
        add("asks_next_rate", "ACCURACY", _ratio(recs, lambda r: r["rules"]["asks_next"], lambda r: True), True)
        add("sentence_count_violation_rate", "ACCURACY", _ratio(recs, lambda r: r["rules"]["sentences_bad"], lambda r: True), False)
        add("exact_match_rate", "ACCURACY", _ratio(recs, lambda r: r["prot_exact"], lambda r: True), True)
        add("forbidden_expression_rate", "SAFETY", _ratio(recs, lambda r: r["rules"]["banned"], lambda r: True), False)
        add("ungrounded_number_rate", "SAFETY", _ratio(recs, lambda r: r["rules"]["new_number"], lambda r: True), False)
        add("json_leak_rate", "PROTECTION", _ratio(recs, lambda r: r["rules"]["json_leak"], lambda r: True), False)
        add("fallback_rate", "PROTECTION", _ratio(recs, lambda r: r["is_fallback"], lambda r: True), False)
        add("schema_valid_raw", "PROTECTION", _ratio(recs, lambda r: r["raw_valid"], lambda r: True), True)
        iv = _ratio(recs, lambda r: r["outcome"] == "fallback", lambda r: True)
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
    seen = _ratio([r for s, r in pairs if sample_qs(s) and qs_key(sample_qs(s)) in seen_sets], lambda r: r["prot_exact"], lambda r: True)
    unseen = _ratio([r for s, r in pairs if sample_qs(s) and qs_key(sample_qs(s)) not in seen_sets], lambda r: r["prot_exact"], lambda r: True)
    if seen and unseen:
        metrics.append(_m("coverage_seen_unseen_gap", "COVERAGE", seen[0] - unseen[0], seen[1] + unseen[1], False, "score"))
    if regression_recs:
        n = len(regression_recs)
        ok = sum(1 for r in regression_recs if r["prot_exact"])
        metrics.append(_m("regression_fixed", "REGRESSION", ok, n, True, "count"))
        metrics.append(_m("regression_broken", "REGRESSION", n - ok, n, False, "count"))
    notes = [
        "합격 판정과 임계치는 없다. 값은 계산한 그대로이며 평가 백엔드는 서빙과 다르고 제약 디코딩을 쓰지 않았다.",
        "coverage_seen_unseen_gap은 (본 문항 세트 정답 일치율 - 보지 못한 문항 세트 정답 일치율)이며 샘플에 question_set이 있고 둘 다 표본이 있을 때만 낸다. 일치는 정답 출력과의 완전 일치다.",
        "v1의 위험 신호 지표(risk_signal_*)와 의도·항목 지표는 v2 출력에 대응이 없어 없앴다. v2에서 위험 신호를 어떻게 표현할지는 정해지지 않았다(미정, 헤드).",
    ]
    if role == "LORA_A":
        notes += ["LoRA-A 지표는 학습 서버 score_a_v2.py와 같은 규칙으로 계산했다: 칸(key)별 비교(values 순서 무시), 칸 정답은 value·status·save가 모두 같을 때, 5와 5.0은 같고 true와 1은 다름, 형식 실패 행의 정답 칸도 분모에 넣는다. precision은 따로 센다.",
                  "schema_ok_rate는 계약 ActionExtraction 스키마(allOf 포함)를 통과한 비율이고, score_a_v2의 schema_ok(느슨한 검사)보다 엄격하다. LoRA-A는 보호가 없어 통과하지 못하면 서빙에서는 503 OUTPUT_INVALID(D75)다(output_invalid).",
                  "targets/next_item 의미 규칙(turn-engine validate_extraction)은 이 평가에서 계산하지 않았다.",
                  "extra_keys, dup_keys, targets_extra_rows의 n은 평가한 행 수다."]
    else:
        notes += ["LoRA-B 문장 규칙(끝맺음, 다음 질문, 새 숫자, 금지 표현, 문장 수, JSON 출력)은 기계 검사이며 사람 검수가 아니다. 금지 표현 목록과 문장 수 범위는 학습 서버 평가의 값을 따른 잠정값이다(미정).",
                  "ungrounded_number_rate는 요청 JSON에 없는 숫자(아라비아 숫자와 한글 수사 일부)를 말한 비율이다. 한글 수사 해석은 완전하지 않다.",
                  "exact_match_rate는 정답 문장과 글자 단위로 같은 비율이다. 문장 생성의 품질 지표로 쓰기에는 약하다.",
                  "length_p95_ms는 생략했다: 이 평가는 응답 지연을 측정하지 않는다."]
    if not regression_recs:
        notes.append("regression_fixed/regression_broken은 생략했다: 회귀 평가를 켜지 않았거나 이 역할의 회귀 사례가 없다.")
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
