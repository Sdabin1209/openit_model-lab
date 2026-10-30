"""평가 보고서 컴포넌트: 채점된 SampleRecord 묶음을 받아 상세 보고서(쪼개 보기), 계약 지표 목록, 회귀 절을 낸다(순수 함수).

입력 EvalReportRequest, 출력 EvalReportBundle(schemas/). 구 evaluate(집계)·metrics·metric_names·regression.section을 한 컴포넌트로 묶었다.
왜 하나: 상세 집계, 지표, 회귀 절이 같은 합계(a_rates, _sum_c, 쪼개기 차원)를 쓰고, 컴포넌트끼리 import할 수 없으므로(D117 규칙 3) 쪼개면 복사해야 한다.
"""
from __future__ import annotations

import collections
from typing import Any, Callable, Mapping

from boundary_contracts import metrics as registry_metrics
from boundary_contracts.action_rules import load_rules

# 폐기된 v1 지표 이름(새 보고서에 쓰면 안 된다). 왜: 계약이 지표 체계를 v2로 바꿨다(ADR 0011).
DEPRECATED_V1 = frozenset({"risk_signal_recall", "risk_signal_false_alarm", "intent_accuracy", "item_precision", "item_recall",
                           "target_accuracy", "risk_notice_verbatim_rate", "slot_coverage"})
DEPRECATED_V1_PREFIXES = ("risk_signal",)

# 학습 서버 score_a_v2.py / eval_lora_b_v2.py 이름 중 계약 레지스트리(METRICS.md v2)의 같은 계산 지표와 1:1인 것. 왜: 지표 이름의 정본은 계약
# 레지스트리이고 같은 분자·분모를 다른 이름으로 또 내면 안 된다(ML-01). 보고서에는 오른쪽 레지스트리 이름만 낸다.
# 근거: 레지스트리 doc.meaning과 evaluate.a_rates / metrics.metrics_for의 분자·분모 대조(분모 같음).
SERVER_TO_REGISTRY = {
    "schema_ok_rate": "schema_valid_raw",        # 둘 다 출력 보호 전 스키마 통과율(raw_valid / rows)
    "action_acc": "action_accuracy",
    "next_item_acc": "next_item_accuracy",
    "value_acc": "value_exact_match",            # 값이 정답과 정확히 같은 칸 / 정답 칸
    "status_acc": "status_accuracy",
    "targets_acc": "targets_match",
    "precision_acc": "precision_match",
}
# 레지스트리에 대응이 없는 이 영역의 보조 지표. 최상위 metrics에 내지 않고 ext."model-lab".detail.unregistered_metrics에 둔다.
# 레지스트리 추가 요청: docs/change-requests/CR-L011-metric-registry-additions.md (헤드가 받기 전에는 최상위에 올리지 않는다).
UNREGISTERED_A = frozenset({"action_reason_acc", "cell_acc", "save_acc", "db_row_match_rate", "exact_match_rate", "oos_f1", "key_found_rate"})
UNREGISTERED_B = frozenset({"ends_ok_rate", "asks_next_rate", "sentence_count_violation_rate", "json_leak_rate", "exact_match_rate"})


def violation_codes() -> tuple[str, ...]:
    """semantic_violation_rate가 코드별로 내는 위반 코드. 정본은 계약 action_extraction_rules.json이다(코드 이름을 여기에 베끼지 않는다).
    OUTPUT_SCHEMA는 check_action_extraction이 출력 구조가 ActionExtraction이 아닐 때 내는 코드다. 순서는 이전 구현(turn-engine CODES)과 같게 둔다
    (보고서의 지표 순서가 바뀌지 않게)."""
    r = load_rules()
    return ("OUTPUT_SCHEMA", r["targets"]["code"], r["next_item"]["mismatch_code"], r["next_item"]["skip_order_code"],
            r["save_rule"]["code"], r["unknown_item"]["code"])


def _qs_key(ref: Mapping) -> tuple:
    return (ref["id"], ref["version"])


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


# 왜 nearest-rank: 보간이 필요 없고 표본이 적어도 실제로 관측된 값을 낸다(백분위의 가장 단순한 표준 정의).
def _p95(vals: list[float]) -> float:
    v = sorted(vals)
    return v[max(0, -(-95 * len(v) // 100) - 1)]


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
        # --- METRICS.md v2 이름(정본). 위 *_acc 등은 score_a_v2.py 이름의 보조 값이다.
        def v2(name, num, den, hib, group="ACCURACY"):
            if den:
                out.append(_m(name, group, num / den, den, hib, slice_=slice_))
        v2("value_precision", t.get("value_tp", 0), t.get("pred_values", 0), True)
        v2("value_recall", t.get("value_tp", 0), t.get("gold_keys", 0), True)
        v2("precision_match", t.get("precision", 0), t.get("gold_keys", 0), True)
        v2("targets_match", t.get("targets", 0), t.get("targets_rows", 0), True)
        v2("value_exact_match", t.get("value", 0), t.get("gold_keys", 0), True)
        v2("action_accuracy", t.get("action", 0), t.get("rows", 0), True)
        v2("reason_accuracy", t.get("reason_ok", 0), t.get("reason_rows", 0), True)
        v2("status_accuracy", t.get("status", 0), t.get("gold_keys", 0), True)
        v2("next_item_accuracy", t.get("next_item", 0), t.get("rows", 0), True)      # ACTION slice가 action별 분모다(D96 기본값 3)
        checked = [r["violations"] for r in recs if r.get("violations") is not None]
        v2("semantic_violation_rate", sum(1 for v in checked if v), len(checked), False)
        add("schema_valid_raw", "PROTECTION", _ratio(recs, lambda r: r["raw_valid"], lambda r: True), True)
        add("output_invalid_rate", "PROTECTION", _ratio(recs, lambda r: r["outcome"] == "invalid", lambda r: True), False)
        if recs:
            out.append(_m("output_attempts_mean", "PROTECTION", sum(r["attempts"] for r in recs) / len(recs), len(recs), False, "count", slice_))
        add("schema_ok_rate", "PROTECTION", _ratio(recs, lambda r: r["raw_valid"], lambda r: True), True)
        out.append(_m("output_invalid", "PROTECTION", sum(1 for r in recs if r["outcome"] == "invalid"), len(recs), False, "count", slice_))
    else:
        add("ends_ok_rate", "ACCURACY", _ratio(recs, lambda r: r["rules"]["ends_ok"], lambda r: True), True)
        add("asks_next_rate", "ACCURACY", _ratio(recs, lambda r: r["rules"]["asks_next"], lambda r: True), True)
        add("sentence_count_violation_rate", "ACCURACY", _ratio(recs, lambda r: r["rules"]["sentences_bad"], lambda r: True), False)
        add("exact_match_rate", "ACCURACY", _ratio(recs, lambda r: r["prot_exact"], lambda r: True), True)
        add("forbidden_expression_rate", "SAFETY", _ratio(recs, lambda r: r["rules"]["banned"], lambda r: True), False)
        add("ungrounded_number_rate", "SAFETY", _ratio(recs, lambda r: r["rules"]["new_number"], lambda r: True), False)
        add("material_coverage", "ACCURACY", _ratio(recs, lambda r: r["material_ok"], lambda r: r.get("material_ok") is not None), True)
        if recs:
            out.append(_m("response_length_chars_p95", "ROBUSTNESS", _p95([r["response_len"] for r in recs]), len(recs), False, "count", slice_))   # METRICS.md는 unit=chars지만 스키마 열거에 없다(CR-L009)
        add("json_leak_rate", "PROTECTION", _ratio(recs, lambda r: r["rules"]["json_leak"], lambda r: True), False)
        add("fallback_rate", "PROTECTION", _ratio(recs, lambda r: r["is_fallback"], lambda r: True), False)
        add("schema_valid_raw", "PROTECTION", _ratio(recs, lambda r: r["raw_valid"], lambda r: True), True)
        iv = _ratio(recs, lambda r: r["outcome"] == "fallback", lambda r: True)
        if iv:
            out.append(_m("protection_intervened", "PROTECTION", iv[0] * iv[1], iv[1], False, "count", slice_))
    return out


def split_by_registry(metrics: list[dict]) -> tuple[list[dict], list[dict]]:
    """(최상위에 낼 레지스트리 지표, 레지스트리에 없는 보조 지표). 레지스트리와 같은 계산인 서버 이름은 버린다(레지스트리 이름이 이미 있다).
    왜: 지표 이름의 정본은 계약 레지스트리다(METRICS.md, ML-01). 대응이 없는 이름을 만들어 최상위에 내지 않는다."""
    reg = registry_metrics()
    ok, extra = [], []
    for m in metrics:
        n = m["name"]
        if n in SERVER_TO_REGISTRY:
            continue
        (ok if n in reg and reg[n]["status"] == "ACTIVE" else extra).append(m)
    return ok, extra


def build_metrics(role: str, pairs: list[tuple[dict, dict]], seen_sets: set, *, regression_recs: list[dict] | None = None,
                  constrained_decoding: bool | None = None):
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
    if role == "LORA_A":
        checked = [r["violations"] for r in recs if r.get("violations") is not None]
        for code in violation_codes():       # 위반 코드별 비율. 0도 낸다(위반이 없다는 것을 보고서에서 읽을 수 있게)
            if checked:
                metrics.append(_m("semantic_violation_rate", "ACCURACY", sum(1 for v in checked if code in v) / len(checked), len(checked),
                                  False, slice_=("VIOLATION_CODE", code)))
    if constrained_decoding is not None:      # 켠/끈 값을 구분해 읽게 한다(D96 기본값 4). 한 실행은 한 쪽만 잰다
        for m in [m for m in metrics if m["name"] == "schema_valid_raw" and "slice" not in m]:
            metrics.append({**m, "slice": {"kind": "CONSTRAINED_DECODING", "value": "on" if constrained_decoding else "off"}})
    seen = _ratio([r for s, r in pairs if sample_qs(s) and _qs_key(sample_qs(s)) in seen_sets], lambda r: r["prot_exact"], lambda r: True)
    unseen = _ratio([r for s, r in pairs if sample_qs(s) and _qs_key(sample_qs(s)) not in seen_sets], lambda r: r["prot_exact"], lambda r: True)
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
        "v1 지표(" + ", ".join(sorted(DEPRECATED_V1)) + ")는 폐기했다(METRICS.md v2). v2에서 위험 신호를 어떻게 표현할지는 정해지지 않았다(미정, 헤드).",
        "value_precision/recall은 (key, value, status)가 모두 같은 값만 맞은 것으로 센다(D96 기본값, 학습 담당 확인 전 임시). 형식 실패 행의 정답 칸은 recall 분모에 들어간다. next_item_accuracy는 slice kind=ACTION이 action별 분모다. 합격선은 없다(O11).",
        "score_a_v2.py 이름의 지표(action_acc, cell_acc, value_acc, ...)는 학습 서버 값과 대조하려는 보조 값이며 METRICS.md v2 이름과 같은 분자·분모인 것도 있다.",
    ]
    if role == "LORA_A":
        notes += ["LoRA-A 지표는 학습 서버 score_a_v2.py와 같은 규칙으로 계산했다: 칸(key)별 비교(values 순서 무시), 칸 정답은 value·status·save가 모두 같을 때, 5와 5.0은 같고 true와 1은 다름, 형식 실패 행의 정답 칸도 분모에 넣는다. precision은 따로 센다.",
                  "schema_ok_rate는 계약 ActionExtraction 스키마(allOf 포함)를 통과한 비율이고, score_a_v2의 schema_ok(느슨한 검사)보다 엄격하다. LoRA-A는 보호가 없어 통과하지 못하면 서빙에서는 503 OUTPUT_INVALID(D75)다(output_invalid).",
                  "semantic_violation_rate는 계약 check_action_extraction(문진 스키마 question_flow의 문항 순서표)이 위반을 낸 출력의 비율이다. 형식 실패 출력은 분모에서 빠지고 output_invalid_rate가 센다. 정답과 비교하지 않는다(D97).",
                  "output_invalid_rate는 평가 백엔드(제약 디코딩 없음)에서 잰 값이며 서빙(vLLM) 값과 다를 수 있다. 백엔드는 evaluation_backend에 적는다.",
                  "extra_keys, dup_keys, targets_extra_rows의 n은 평가한 행 수다."]
    else:
        notes += ["LoRA-B 문장 규칙(끝맺음, 다음 질문, 새 숫자, 금지 표현, 문장 수, JSON 출력)은 기계 검사이며 사람 검수가 아니다. 금지 표현 목록과 문장 수 범위는 학습 서버 평가의 값을 따른 잠정값이다(미정).",
                  "ungrounded_number_rate는 재료(known, ask, previous, current, hold, next.text, user_utterance)에 없는 숫자(아라비아 숫자와 한글 수사 일부)를 말한 비율이다. 런타임 검사가 아니라 평가 지표로만 잰다(D94). 한글 수사 해석은 완전하지 않고 ask의 영문 괄호는 따로 처리하지 않는다.",
                  "material_coverage는 action별 필수 재료 표(text_rules._REQUIRED, 잠정, 학습 담당 확인 전)를 핵심 단어·숫자 포함으로 판정한 비율이다. 재료가 없는 행은 분모에서 뺐다.",
                  "forbidden_expression_rate의 금지 표현 목록은 학습 서버 평가 값을 따른 잠정 목록이며 소유자는 정해지지 않았다(D59). response_length_chars_p95는 nearest-rank 백분위다.",
                  "exact_match_rate는 정답 문장과 글자 단위로 같은 비율이다. 문장 생성의 품질 지표로 쓰기에는 약하다.",
                  "length_p95_ms는 생략했다: 이 평가는 응답 지연을 측정하지 않는다."]
    if not regression_recs:
        notes.append("regression_fixed/regression_broken은 생략했다: 회귀 평가를 켜지 않았거나 이 역할의 회귀 사례가 없다.")
    return metrics, notes


REPORT_VERSION = 2
NOTICE = ("통과 임계치와 지표 선정은 정해지지 않았다(O11). 이 보고서는 계산한 값만 담으며 합격 여부를 판단하지 않는다.")


def sample_qs(sample: Mapping) -> Mapping | None:
    return sample.get("question_set")


def _qs_label(sample: Mapping) -> str:
    q = sample_qs(sample)
    return f"{q['id']}@{q['version']}" if q else "(미상)"


def sample_dims(role: str, sample: Mapping, seen_sets: set) -> dict[str, set]:
    """샘플이 속하는 쪼개기 값들(차원 -> 값 집합)."""
    d: dict[str, set] = collections.defaultdict(set)
    q = sample_qs(sample)
    d["question_set"].add(_qs_label(sample))
    d["seen"].add("unknown" if not q else ("seen" if _qs_key(q) in seen_sets else "unseen"))
    exp, inp = sample["expected"], sample["input"]
    if role == "LORA_A":
        d["action"].add(exp.get("action", "?"))
        d["reason"].add(exp.get("reason", "(없음)"))
        d["question_id"].add(inp.get("question_id", "?"))
    else:
        d["action"].add(inp.get("action", "?"))
        d["reason"].add(inp.get("reason", "(없음)"))
        d["next_type"].add((inp.get("next") or {}).get("type", "?"))
    return d


def _sum_c(recs: list[dict]) -> collections.Counter:
    t: collections.Counter = collections.Counter()
    for r in recs:
        t.update(r["c"])
    return t


def a_rates(t: Mapping) -> dict[str, float | None]:
    n, k = t.get("rows", 0), t.get("gold_keys", 0)
    r = lambda a, b: (a / b) if b else None   # noqa: E731
    tp, fp, fn = t.get("oos_tp", 0), t.get("oos_fp", 0), t.get("oos_fn", 0)
    prec, rec = r(tp, tp + fp), r(tp, tp + fn)
    return {"schema_ok_rate": r(t.get("schema_ok", 0), n), "action_acc": r(t.get("action", 0), n),
            "action_reason_acc": r(t.get("reason", 0), n), "next_item_acc": r(t.get("next_item", 0), n),
            "key_found_rate": r(t.get("key_found", 0), k), "cell_acc": r(t.get("cell", 0), k),
            "value_acc": r(t.get("value", 0), k), "status_acc": r(t.get("status", 0), k), "save_acc": r(t.get("save", 0), k),
            "precision_acc": r(t.get("precision", 0), k), "db_row_match_rate": r(t.get("db_row", 0), n),
            "targets_acc": r(t.get("targets", 0), t.get("targets_rows", 0)), "note_presence_acc": r(t.get("note_presence", 0), n),
            "exact_match_rate": r(t.get("exact", 0), n), "oos_precision": prec, "oos_recall": rec,
            "oos_f1": (2 * prec * rec / (prec + rec)) if prec and rec else (0.0 if tp + fn else None)}


def aggregate(recs: list[dict], role: str) -> dict:
    n = len(recs)
    a: dict[str, Any] = {"n": n}
    if n == 0:
        return a

    def cnt(f: Callable[[dict], bool]) -> int: return sum(1 for r in recs if f(r))
    a["raw_valid"] = cnt(lambda r: r["raw_valid"])
    a["prot_exact"] = cnt(lambda r: r["prot_exact"])
    a["outcomes"] = dict(sorted(collections.Counter(r["outcome"] for r in recs).items()))
    a["attempts"] = {str(k): v for k, v in sorted(collections.Counter(r["attempts"] for r in recs).items())}
    prot: collections.Counter = collections.Counter()
    for r in recs:
        for name, c in r["protections"]:
            prot[name] += c
    a["protections"] = dict(sorted(prot.items()))
    a["rates"] = {"raw_valid": a["raw_valid"] / n, "prot_exact": a["prot_exact"] / n}
    if role == "LORA_A":
        t = _sum_c(recs)
        a["counts"] = dict(sorted(t.items()))
        checked = [r["violations"] for r in recs if r.get("violations") is not None]
        a["semantic_violations"] = {"checked": len(checked), "violating": sum(1 for v in checked if v),
                                    "by_code": dict(sorted(collections.Counter(c for v in checked for c in v).items()))}
        a["rates"].update({k: v for k, v in a_rates(t).items() if v is not None})
    else:
        a["raw_exact"] = cnt(lambda r: r["raw_exact"])
        a["rates"]["raw_exact"] = a["raw_exact"] / n
        a["fallback"] = cnt(lambda r: r["is_fallback"])
        a["response_len_mean"] = sum(r["response_len"] for r in recs) / n
        a["rule_violations"] = {"ends_not_ok": cnt(lambda r: not r["rules"]["ends_ok"]), "asks_next_missing": cnt(lambda r: not r["rules"]["asks_next"]),
                                "new_number": cnt(lambda r: r["rules"]["new_number"]), "banned": cnt(lambda r: r["rules"]["banned"]),
                                "sentences_bad": cnt(lambda r: r["rules"]["sentences_bad"]), "json_leak": cnt(lambda r: r["rules"]["json_leak"])}
    return a


def build_report(role: str, recs: list[tuple[dict, dict]], *, seen_sets: set, eval_files: list[dict],
                 max_listed_failures: int = 20, context: Mapping | None = None):
    """recs = [(sample, SampleRecord)]. 상세 보고서(구 evaluate.build_report)."""
    all_recs = [r for _, r in recs]
    dims: dict[str, dict[str, list]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for s, r in recs:
        for dim, vals in sample_dims(role, s, seen_sets).items():
            for v in vals:
                dims[dim][v].append(r)
    seen_r = [r for s, r in recs if sample_qs(s) and _qs_key(sample_qs(s)) in seen_sets]
    unseen_r = [r for s, r in recs if sample_qs(s) and _qs_key(sample_qs(s)) not in seen_sets]
    unseen_sets = sorted({_qs_label(s) for s, _ in recs if sample_qs(s) and _qs_key(sample_qs(s)) not in seen_sets})
    failures = [{"sample_id": s["sample_id"], "question_set": _qs_label(s), "raw_valid": r["raw_valid"], "raw_errors": r["raw_errors"],
                 "outcome": r["outcome"], "prot_exact": r["prot_exact"]} for s, r in recs if not r["prot_exact"] or not r["raw_valid"]]
    report = {
        "report_version": REPORT_VERSION, "role": role, "notice": NOTICE,
        "context": dict(context or {}),
        "eval_files": eval_files,
        "overall": aggregate(all_recs, role),
        "seen_question_sets": aggregate(seen_r, role),
        "unseen_question_sets": {"question_sets": unseen_sets, "aggregate": aggregate(unseen_r, role),
                                 "note": "학습·검증에 쓰지 않은 문항 세트의 샘플(D32). 비어 있으면 이 평가 데이터에는 보지 못한 문항 세트가 없다(또는 샘플에 문항 세트가 적혀 있지 않다)."},
        "slices": {dim: {v: aggregate(rs, role) for v, rs in sorted(vals.items())} for dim, vals in sorted(dims.items())},
        "failures": {"count": len(failures), "listed": failures[:max_listed_failures]},
    }
    return report


def report_bytes(report: Mapping) -> bytes:
    import json
    return (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def regression_section(role: str, rset: dict, recs: list, *, max_listed: int = 20) -> dict:
    """보고서의 `regression` 절."""
    ss = rset["samples"][role]
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
