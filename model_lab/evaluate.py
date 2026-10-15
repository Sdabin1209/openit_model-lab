"""평가기. 서빙과 같은 경로(model-runtime ModelSession의 *_raw와 보호 적용 parse/generate)로 돌린다.

지표 값을 계산해 보고서에 담기만 한다. 통과 임계치, 합격 여부, 지표 선정은 하지 않는다(O11, 사용자 결정).
코드에도 설정에도 기본 임계치가 없다. 보고서의 수치는 '계산한 값의 후보'이지 선정된 지표가 아니다.

쪼개 보기(D13): 문항 세트, 본 문항/보지 못한 문항, 문항, 슬롯 유형, 단위, 범위, 의도, 잡음 유형(파서)
행동, 문항 세트, 본/보지 못한 문항, 문항(생성기). 보지 못한 문항 세트 평가는 따로 보고한다(D32).
"""
from __future__ import annotations

import collections
from typing import Any, Callable, Iterable, Mapping

from boundary_contracts import canonical

from .datasets import qs_key

REPORT_VERSION = 1
NOTICE = ("통과 임계치와 지표 선정은 정해지지 않았다(O11). 이 보고서는 계산한 값만 담으며 합격 여부를 판단하지 않는다.")

ITEM_KEYS = ("target", "intent", "value", "progress_op", "guide_id")


def _item_sig(it: Mapping) -> str:
    return canonical.canonicalize({k: it.get(k) for k in ITEM_KEYS}).decode()


def _items(output: Any) -> list | None:
    if isinstance(output, Mapping) and isinstance(output.get("items"), list):
        return [i for i in output["items"] if isinstance(i, Mapping)]
    return None


def _same(a: Any, b: Any) -> bool:
    try:
        return canonical.canonicalize(a) == canonical.canonicalize(b)
    except ValueError:
        return False


def _slot_info(sample: Mapping, target: Mapping | None) -> tuple[str, list[str], list[str]]:
    """대상 슬롯의 (유형, 단위 이름들, 단위:범위들). 입력의 awaiting_questions에서 찾는다."""
    if not target:
        return "no_target", [], []
    for q in sample["input"].get("awaiting_questions", []) or []:
        if q.get("question_id") != target.get("question_id"):
            continue
        for s in q.get("slots", []):
            if s.get("slot_id") == target.get("slot_id"):
                if s.get("units"):
                    units = [u["name"] for u in s["units"]]
                    ranges = [f"{u['name']}:{u['range'][0]}-{u['range'][1]}" for u in s["units"] if u.get("range")]
                    return "unit", units, ranges
                if s.get("yes_no_map"):
                    return "yes_no", [], []
                if s.get("choices"):
                    return "choice", [], []
                return "free", [], []
    return "target_not_in_input", [], []


def sample_dims(role: str, sample: Mapping, seen_sets: set) -> dict[str, set]:
    """샘플이 속하는 쪼개기 값들(차원 -> 값 집합)."""
    d: dict[str, set] = collections.defaultdict(set)
    d["question_set"].add(f"{sample['question_set']['id']}@{sample['question_set']['version']}")
    d["seen"].add("seen" if qs_key(sample["question_set"]) in seen_sets else "unseen")
    if role == "LORA_A":
        items = _items(sample["expected"]) or []
        for it in items:
            d["intent"].add(it.get("intent", "?"))
            t = it.get("target")
            if t:
                d["question_id"].add(t["question_id"])
            kind, units, ranges = _slot_info(sample, t)
            d["slot_kind"].add(kind)
            d["unit"].update(units)
            d["range"].update(ranges)
        if not items:
            d["intent"].add("(항목 없음)")
        for n in sample.get("noise_types") or []:
            d["noise_type"].add(n)
    else:
        dr = sample["input"].get("directive", {})
        d["action"].add(dr.get("action", "?"))
        for t in dr.get("targets", []) or []:
            d["question_id"].add(t["question_id"])
        d["has_utterance"].add(str(sample["input"].get("utterance") is not None).lower())
    return d


def evaluate_sample(role: str, session, sample: Mapping) -> dict:
    """한 샘플: 보호 전(raw)과 보호 후 결과."""
    inp = sample["input"]
    if role == "LORA_A":
        raw = session.parse_raw(inp)
        out = session.parse(inp)
    else:
        raw = session.generate_raw(inp)
        out = session.generate(inp)
    info = session.last_call_info
    exp = sample["expected"]
    rec: dict[str, Any] = {
        "raw_valid": bool(raw.valid), "raw_errors": list(raw.errors),
        "outcome": getattr(info, "outcome", "ok"), "attempts": getattr(info, "attempts", 0),
        "protections": [list(p) for p in getattr(info, "protections", ())],
    }
    if role == "LORA_A":
        rec["raw_exact"] = raw.output is not None and _same(raw.output, exp)
        rec["prot_exact"] = _same(out, exp)
        e = collections.Counter(_item_sig(i) for i in (_items(exp) or []))
        for key, o in (("raw", raw.output), ("prot", out)):
            oi = _items(o)
            g = collections.Counter(_item_sig(i) for i in (oi or []))
            tp = sum((e & g).values())
            rec[f"{key}_item_tp"], rec[f"{key}_item_fp"], rec[f"{key}_item_fn"] = tp, sum(g.values()) - tp, sum(e.values()) - tp
    else:
        want = exp.get("response_text")
        rec["raw_exact"] = isinstance(raw.output, Mapping) and raw.output.get("response_text") == want
        rec["prot_exact"] = out.get("response_text") == want
        rec["is_fallback"] = bool(out.get("is_fallback"))
        rec["fallback_matches_expected"] = bool(out.get("is_fallback")) == bool(exp.get("is_fallback"))
        rt = out.get("response_text") or ""
        rec["response_len"] = len(rt)
    return rec


def aggregate(recs: list[dict], role: str) -> dict:
    n = len(recs)
    a: dict[str, Any] = {"n": n}
    if n == 0:
        return a

    def cnt(f: Callable[[dict], bool]) -> int: return sum(1 for r in recs if f(r))
    a["raw_valid"] = cnt(lambda r: r["raw_valid"])
    a["raw_exact"] = cnt(lambda r: r["raw_exact"])
    a["prot_exact"] = cnt(lambda r: r["prot_exact"])
    a["outcomes"] = dict(sorted(collections.Counter(r["outcome"] for r in recs).items()))
    a["attempts"] = {str(k): v for k, v in sorted(collections.Counter(r["attempts"] for r in recs).items())}
    prot = collections.Counter()
    for r in recs:
        for name, c in r["protections"]:
            prot[name] += c
    a["protections"] = dict(sorted(prot.items()))
    a["rates"] = {k: a[k] / n for k in ("raw_valid", "raw_exact", "prot_exact")}
    # 보호 전 출력의 스키마·규칙 유효(raw_valid)와 보호 개입 건수를 따로 담는다(D48). 임계치는 없다.
    a["protection_interventions"] = {"samples_with_protection": cnt(lambda r: r["outcome"] in ("repaired", "fallback")),
                                     "by_outcome": {k: v for k, v in a["outcomes"].items() if k != "ok"}}
    if role == "LORA_A":
        for key in ("raw", "prot"):
            tp, fp, fn = (sum(r[f"{key}_item_{x}"] for r in recs) for x in ("tp", "fp", "fn"))
            a[f"{key}_items"] = {"tp": tp, "fp": fp, "fn": fn,
                                 "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None}
    else:
        a["fallback"] = cnt(lambda r: r["is_fallback"])
        a["fallback_matches_expected"] = cnt(lambda r: r["fallback_matches_expected"])
        a["response_len_mean"] = sum(r["response_len"] for r in recs) / n
    return a


def build_report(role: str, session, samples: list[dict], *, seen_sets: set, eval_files: list[dict],
                 max_listed_failures: int = 20, context: Mapping | None = None) -> dict:
    recs: list[tuple[dict, dict]] = []
    for s in samples:
        recs.append((s, evaluate_sample(role, session, s)))
    all_recs = [r for _, r in recs]
    dims: dict[str, dict[str, list]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for s, r in recs:
        for dim, vals in sample_dims(role, s, seen_sets).items():
            for v in vals:
                dims[dim][v].append(r)
    seen_r = [r for s, r in recs if qs_key(s["question_set"]) in seen_sets]
    unseen_r = [r for s, r in recs if qs_key(s["question_set"]) not in seen_sets]
    unseen_sets = sorted({f"{s['question_set']['id']}@{s['question_set']['version']}"
                          for s, _ in recs if qs_key(s["question_set"]) not in seen_sets})
    failures = [{"sample_id": s["sample_id"], "question_set": f"{s['question_set']['id']}@{s['question_set']['version']}",
                 "raw_valid": r["raw_valid"], "raw_errors": r["raw_errors"], "outcome": r["outcome"],
                 "prot_exact": r["prot_exact"]} for s, r in recs if not r["prot_exact"] or not r["raw_valid"]]
    report = {
        "report_version": REPORT_VERSION, "role": role, "notice": NOTICE,
        "context": dict(context or {}),
        "eval_files": eval_files,
        "overall": aggregate(all_recs, role),
        "seen_question_sets": aggregate(seen_r, role),
        "unseen_question_sets": {"question_sets": unseen_sets, "aggregate": aggregate(unseen_r, role),
                                 "note": "학습·검증에 쓰지 않은 문항 세트의 샘플(D32). 비어 있으면 이 평가 데이터에는 보지 못한 문항 세트가 없다."},
        "slices": {dim: {v: aggregate(rs, role) for v, rs in sorted(vals.items())} for dim, vals in sorted(dims.items())},
        "failures": {"count": len(failures), "listed": failures[:max_listed_failures]},
    }
    return report


def report_bytes(report: Mapping) -> bytes:
    import json
    return (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
