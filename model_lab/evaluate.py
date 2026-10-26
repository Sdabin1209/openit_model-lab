"""평가기(2.0.0, D74). 서빙과 같은 경로(model-runtime ModelSession의 extract_raw / respond_raw·respond)로 돌린다.

LoRA-A(ActionExtraction)는 보호가 없다(D75): 형식을 못 지키면 서빙은 ModelOutputInvalid(503)이므로 평가에서도 '형식 실패'이고,
채점은 학습 서버의 score_a_v2.py와 같은 규칙이다(칸별 비교, values 순서 무시, 5와 5.0은 같고 true와 1은 다름, save가 true인 칸의 DB 값 일치,
형식 실패 행의 정답 칸도 분모에 넣음). LoRA-B(ResponseText)는 형식 실패 때 대체 문장이 나가며(is_fallback), 문장 규칙은 text_rules.py.
지표 값을 계산해 보고서에 담기만 한다. 통과 임계치, 합격 여부, 지표 선정은 하지 않는다(O11).
코드에도 설정에도 기본 임계치가 없다. 보고서의 수치는 '계산한 값의 후보'이지 선정된 지표가 아니다.

쪼개 보기(D13): 문항 세트, 본/보지 못한 문항 세트(D32), 정답 action·reason, 입력 문항(A), action·reason·다음 유형(B).
샘플에 question_set이 없으면(서버 데이터) 쪼개기에서 '미상'으로 둔다. 파이프라인이 파일의 문항 세트가 하나면 채워 준다.
"""
from __future__ import annotations

import collections
from typing import Any, Callable, Mapping

from .datasets import qs_key
from .text_rules import check_response

REPORT_VERSION = 2
NOTICE = ("통과 임계치와 지표 선정은 정해지지 않았다(O11). 이 보고서는 계산한 값만 담으며 합격 여부를 판단하지 않는다.")
OOS = frozenset({"SKIP", "REDIRECT"})
VALUE_FIELDS = ("key", "value", "precision", "status", "save")
B_FLAGS = ("ends_ok", "asks_next", "new_number", "banned", "sentences_bad", "json_leak")


def same(x: Any, y: Any) -> bool:
    """score_a_v2.same과 같은 규칙: 불리언은 형이 같아야 하고, 정수와 소수는 값으로 비교한다."""
    if isinstance(x, bool) or isinstance(y, bool):
        return type(x) is type(y) and x == y
    if isinstance(x, int) and isinstance(y, int):
        return x == y
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        try:
            return abs(x - y) < 1e-9
        except OverflowError:
            return False
    return x == y


def _by_key(a: Mapping) -> tuple[dict, int]:
    d: dict = {}
    dup = 0
    for v in a.get("values") or []:
        if isinstance(v, Mapping) and isinstance(v.get("key"), str):
            if v["key"] in d:
                dup += 1
            else:
                d[v["key"]] = v
    return d, dup


def _targets_same(g, p) -> bool:
    p = [] if p is None else p
    if not isinstance(p, list) or not all(isinstance(x, str) for x in p):
        return False
    return sorted(g or []) == sorted(p)


def _db_same(p: dict, g: dict) -> bool:
    return p.keys() == g.keys() and all(same(p[k], g[k]) for k in g)


def score_a(gold: Mapping, pred: Mapping | None) -> dict[str, int]:
    """한 행의 계수(합산해 지표를 만든다). pred가 None이거나 형식 실패면 형식 실패로 센다."""
    gv, _ = _by_key(gold)
    gact = gold["action"]
    gt = gold.get("targets")
    c: dict[str, int] = collections.Counter()
    c["rows"] = 1
    c["gold_keys"] = len(gv)
    c["targets_rows"] = int(bool(gt))
    c["note_rows"] = 1
    if pred is None:
        c["schema_fail"] = 1
        c["oos_fn" if gact in OOS else "oos_fp"] += 1     # 못 읽으면 OOS 판정도 틀림(score_a_v2와 같은 규칙)
        return dict(c)
    c["schema_ok"] = 1
    pact = pred["action"]
    pv, dup = _by_key(pred)
    c["action"] = int(pact == gact)
    c["reason"] = int(pact == gact and pred.get("reason") == gold.get("reason"))
    c["next_item"] = int(pred.get("next_item") == gold.get("next_item"))
    c["dup_keys"], c["extra_keys"] = dup, len(pv.keys() - gv.keys())
    for k, gc in gv.items():
        pc = pv.get(k)
        if pc is None:
            continue
        v_ok, s_ok, sv_ok = same(pc.get("value"), gc["value"]), pc.get("status") == gc["status"], pc.get("save") is gc["save"]
        c["key_found"] += 1
        c["value"] += v_ok
        c["status"] += s_ok
        c["save"] += sv_ok
        c["precision"] += pc.get("precision") == gc["precision"]
        c["cell"] += v_ok and s_ok and sv_ok
    c["db_row"] = int(_db_same({k: v.get("value") for k, v in pv.items() if v.get("save") is True},
                               {k: v.get("value") for k, v in gv.items() if v.get("save") is True}))
    t_ok = _targets_same(gt, pred.get("targets"))
    c["targets"] = int(bool(gt) and t_ok)
    c["targets_extra_rows"] = int(not gt and not t_ok)
    c["note_presence"] = int(bool(gold.get("note")) == bool(pred.get("note")))
    c["exact"] = int(pact == gact and pred.get("reason") == gold.get("reason") and pred.get("next_item") == gold.get("next_item")
                     and t_ok and pv.keys() == gv.keys() and dup == 0
                     and all(same(pv[k].get("value"), gv[k]["value"]) and pv[k].get("status") == gv[k]["status"]
                             and pv[k].get("precision") == gv[k]["precision"] and pv[k].get("save") is gv[k]["save"] for k in gv))
    go, po = gact in OOS, pact in OOS
    c["oos_tp"], c["oos_fp"], c["oos_fn"] = int(go and po), int(po and not go), int(go and not po)
    return dict(c)


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
    d["seen"].add("unknown" if not q else ("seen" if qs_key(q) in seen_sets else "unseen"))
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


def evaluate_sample(role: str, session, sample: Mapping) -> dict:
    """한 샘플의 기록. A는 extract_raw 한 번(서빙 경로와 같은 재시도), B는 raw와 보호 적용 결과."""
    inp, exp = sample["input"], sample["expected"]
    if role == "LORA_A":
        raw = session.extract_raw(inp)
        info = session.last_call_info
        rec: dict[str, Any] = {"raw_valid": bool(raw.valid), "raw_errors": list(raw.errors),
                               "outcome": "ok" if raw.valid else "invalid", "attempts": getattr(info, "attempts", 0),
                               "protections": []}
        rec["c"] = score_a(exp, raw.output if raw.valid and isinstance(raw.output, Mapping) else None)
        rec["prot_exact"] = bool(rec["c"].get("exact"))
        return rec
    raw = session.respond_raw(inp)
    out = session.respond(inp)
    info = session.last_call_info
    text = out.text
    rec = {"raw_valid": bool(raw.valid), "raw_errors": list(raw.errors), "outcome": getattr(info, "outcome", "ok"),
           "attempts": getattr(info, "attempts", 0), "protections": [list(p) for p in getattr(info, "protections", ())],
           "is_fallback": bool(out.is_fallback), "response_len": len(text)}
    rec["raw_exact"] = isinstance(raw.output, str) and raw.output == exp
    rec["prot_exact"] = text == exp
    rec["rules"] = check_response(inp, text)
    return rec


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


def build_report(role: str, session, samples: list[dict], *, seen_sets: set, eval_files: list[dict],
                 max_listed_failures: int = 20, context: Mapping | None = None, return_records: bool = False):
    recs: list[tuple[dict, dict]] = [(s, evaluate_sample(role, session, s)) for s in samples]
    all_recs = [r for _, r in recs]
    dims: dict[str, dict[str, list]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for s, r in recs:
        for dim, vals in sample_dims(role, s, seen_sets).items():
            for v in vals:
                dims[dim][v].append(r)
    seen_r = [r for s, r in recs if sample_qs(s) and qs_key(sample_qs(s)) in seen_sets]
    unseen_r = [r for s, r in recs if sample_qs(s) and qs_key(sample_qs(s)) not in seen_sets]
    unseen_sets = sorted({_qs_label(s) for s, _ in recs if sample_qs(s) and qs_key(sample_qs(s)) not in seen_sets})
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
    return (report, recs) if return_records else report


def report_bytes(report: Mapping) -> bytes:
    import json
    return (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
