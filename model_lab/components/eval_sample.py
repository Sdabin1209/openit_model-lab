"""평가 채점 컴포넌트: 샘플 하나의 정답(expected)과 모델 호출 결과(InferenceResult)를 받아 SampleRecord를 낸다(순수 함수).

입력 스키마 EvalSampleRequest, 출력 스키마 SampleRecord(schemas/). 모델 호출은 이 컴포넌트가 하지 않는다(model_infer가 InferenceResult를 낸다, wiring이 잇는다).
LoRA-A(ActionExtraction)는 보호가 없다(D75): 형식을 못 지키면 서빙은 ModelOutputInvalid(503)이므로 평가에서도 '형식 실패'이고,
채점은 학습 서버의 score_a_v2.py와 같은 규칙이다(칸별 비교, values 순서 무시, 5와 5.0은 같고 true와 1은 다름, save가 true인 칸의 DB 값 일치,
형식 실패 행의 정답 칸도 분모에 넣음). LoRA-B(ResponseText)는 형식 실패 때 대체 문장이 나가며(is_fallback), 문장 규칙은 아래 "LoRA-B 문장 규칙" 절.
통과 임계치, 합격 여부, 지표 선정은 하지 않는다(O11)."""
from __future__ import annotations

import collections
import re
from functools import lru_cache
from typing import Any, Mapping

import survey_schema
from boundary_contracts import BoundaryError, validator
from boundary_contracts.action_rules import check_action_extraction

OOS = frozenset({"SKIP", "REDIRECT"})


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
    c["reason_rows"] = int(gold.get("reason") is not None)     # reason_accuracy의 분모: 정답에 reason이 있는 행(METRICS.md v2)
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
    c["reason_ok"] = int(c["reason_rows"] and pred.get("reason") == gold.get("reason"))
    # value_precision의 분모: 모델이 낸 values 항목 수(중복 key도 센다. 같은 칸을 두 번 내면 정밀도가 깎인다)
    c["pred_values"] = dup + len(pv)
    for k, gc in gv.items():
        pc = pv.get(k)
        if pc is None:
            continue
        # 왜: (key, value, status)가 모두 같아야 맞은 값이다. 키만 맞은 경우를 세면 오답을 놓친다(D96 기본값 1, 분류 평가의 표준 정의).
        c["value_tp"] += same(pc.get("value"), gc["value"]) and pc.get("status") == gc["status"]
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



def semantic_violations(inp: Mapping, output, flow: Mapping | None = None) -> list[str] | None:
    """계약 check_action_extraction(boundary_contracts.action_rules)이 낸 위반 코드(중복 없이 정렬). 검사할 수 없으면 None(형식 실패 출력, 입력 계약 위반).
    왜: 서비스가 쓰는 것과 같은 검증기로 재야 지표가 운영 실패율과 같은 뜻이다(D97). 형식 실패 행은 출력이 없어 분모에서 빠지고
    output_invalid_rate가 따로 센다. flow의 기본값은 문진 스키마 question_flow(학습 데이터셋 v2의 문항 순서·건너뛰기)이며 서비스 동작의 근거는 아니다(D99, D109)."""
    if not isinstance(output, Mapping):
        return None
    try:
        return sorted({v.code for v in check_action_extraction(dict(inp), dict(output), dict(flow or default_flow()))})
    except BoundaryError:
        return None





@lru_cache(maxsize=1)
def default_flow() -> dict:
    data = survey_schema.load_data("question_flow")
    flow = {"order": list(data["order"]), "skips": [dict(s) for s in data["skips"]]}
    validator("QuestionFlowTable").validate(flow)
    return flow

# ---- LoRA-B 문장 규칙 (구 text_rules.py) ----
BANNED = ("!", "진단해", "처방", "권장", "추천드", "조언", "위험합", "괜찮으실 거", "저장했", "기록했", "기록해 두었", "확정했", "완료했")
_QUESTION_END = re.compile(r"(\?|까요|나요|가요|죠|지요|니까|세요|주세요|하세요)\s*[.?]?\s*$")
_STOP = {"있으신가요", "있으세요", "있나요", "하시나요", "드시나요", "인가요", "하세요", "주세요", "그리고", "지금", "현재", "혹시"}
_DIG = {"영": 0, "공": 0, "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_UNIT = {"십": 10, "백": 100, "천": 1000}
_SINO = re.compile(r"[영공일이삼사오육칠팔구십백천]+")
_NATIVE = {"열": "10", "스물": "20", "스무": "20", "서른": "30", "마흔": "40", "쉰": "50"}
_NATIVE_RE = re.compile(r"(?<![가-힣])(열|스물|스무|서른|마흔|쉰)(?=\s?(?:개|살|시|분|명|잔|병|갑|년)|[^가-힣]|$)")
_COUNTER_RE = re.compile(r"(?<![가-힣])([일이삼사오육칠팔구])(?=\s?(?:년|개월|살|세|시간|분|개비|개))")


def _sino(m: re.Match) -> str:
    w = m.group(0)
    if not any(c in _UNIT for c in w):      # '일주일', '이번' 같은 일반 단어일 수 있어 십·백·천이 있을 때만 수로 본다
        return w
    total = cur = 0
    for c in w:
        if c in _DIG:
            cur = _DIG[c]
        else:
            total += (cur or 1) * _UNIT[c]
            cur = 0
    return str(total + cur)


def numbers(text: str) -> set[str]:
    s = _NATIVE_RE.sub(lambda m: _NATIVE[m.group(1)], text or "")
    s = _COUNTER_RE.sub(lambda m: str(_DIG[m.group(1)]), s)
    return set(re.findall(r"\d+(?:\.\d+)?", _SINO.sub(_sino, s)))


def _keywords(text: str | None) -> list[str]:
    return [w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", text or "") if w not in _STOP][:6]


# 재료 = B가 말할 내용을 담은 입력 필드(METRICS.md v2 ungrounded_number_rate). topic·note는 B가 말하지 않는 서술이라 뺀다.
MATERIAL_KEYS = ("known", "ask", "previous", "current", "hold", "user_utterance")


def materials(request: Mapping) -> list[str]:
    out = [request[k] for k in MATERIAL_KEYS if isinstance(request.get(k), str)]
    nt = (request.get("next") or {}).get("text")
    return out + ([nt] if isinstance(nt, str) else [])


def _covered(material: str, text: str) -> bool:
    """재료를 말했는가: 핵심 단어가 일부(1/3, 최소 1개) 들어 있고 재료의 숫자가 빠지지 않았다. 말을 바꿔 전하는 B를 글자 일치로 벌하지 않으려는 잠정 규칙이다."""
    kw = _keywords(material)
    return (not kw or sum(k in text for k in kw) >= max(1, len(kw) // 3)) and numbers(material) <= numbers(text)


# action별로 말해야 할 재료 필드. 잠정값: 학습 데이터 규칙(IO_SPEC)의 표를 읽지 못했고 담당자 확인 전이다(D96, METRICS.md).
_REQUIRED = {"START": ("next",), "NEXT": ("known", "next"), "CORRECT": ("known", "next"), "REASK": ("ask",),
             "CONFIRM": ("previous", "current", "ask"), "HOLD": ("hold", "next"), "SKIP": ("hold", "next"), "REDIRECT": ("next",)}


def material_coverage(request: Mapping, text: str) -> bool | None:
    """말해야 할 재료를 빠짐없이 말했으면 True. 재료가 하나도 없으면 None(분모에서 뺀다). END의 next는 재료가 아니다."""
    need: list[str] = []
    for f in _REQUIRED.get(request.get("action"), ()):
        v = (request.get("next") or {}).get("text") if f == "next" else request.get(f)
        if isinstance(v, str) and v:
            need.append(v)
    return all(_covered(m, text) for m in need) if need else None


def check_response(request: Mapping, text: str) -> dict[str, bool]:
    """각 항목이 True면 규칙 위반이 아니라 '해당 현상이 있음'이 아니다: ends_ok, asks_next는 True가 좋고, 나머지 *_bad류는 True가 나쁘다."""
    nxt = request.get("next") or {}
    ntype = nxt.get("type")
    if ntype in ("NEXT", "SAME"):
        ends_ok = bool(_QUESTION_END.search(text))
    elif ntype == "END":
        ends_ok = "?" not in text
    else:
        ends_ok = True
    kw = _keywords(nxt.get("text"))
    asks_next = (sum(k in text for k in kw) >= max(1, len(kw) // 3)) if ntype == "NEXT" and kw else True
    sents = [x for x in re.split(r"(?<=[.?!])\s+", text.strip()) if x]
    limit = 5 if request.get("action") == "START" else 4
    return {"ends_ok": ends_ok, "asks_next": asks_next,
            "new_number": bool(numbers(text) - numbers(" ".join(materials(request)))),
            "banned": any(b in text for b in BANNED),
            "sentences_bad": not (1 <= len(sents) <= limit),
            "json_leak": text.lstrip().startswith("{")}


def score(request: Mapping) -> dict:
    """EvalSampleRequest {role, sample, inference, flow?} -> SampleRecord. 입력은 wiring이 스키마로 검증해 넘긴다(R7)."""
    role, sample, inf = request["role"], request["sample"], request["inference"]
    flow = request.get("flow")
    inp, exp = sample["input"], sample["expected"]
    if role == "LORA_A":
        valid = bool(inf["raw_valid"])
        rec: dict[str, Any] = {"raw_valid": valid, "raw_errors": list(inf["raw_errors"]),
                               "outcome": "ok" if valid else "invalid", "attempts": inf["attempts"], "protections": []}
        out = inf.get("raw_output")
        rec["c"] = score_a(exp, out if valid and isinstance(out, Mapping) else None)
        rec["violations"] = semantic_violations(inp, out, flow) if valid else None
        rec["prot_exact"] = bool(rec["c"].get("exact"))
        return rec
    text = inf["text"]
    rec = {"raw_valid": bool(inf["raw_valid"]), "raw_errors": list(inf["raw_errors"]), "outcome": inf["outcome"],
           "attempts": inf["attempts"], "protections": [list(p) for p in inf["protections"]],
           "is_fallback": bool(inf["is_fallback"]), "response_len": len(text)}
    rec["raw_exact"] = isinstance(inf.get("raw_output"), str) and inf["raw_output"] == exp
    rec["prot_exact"] = text == exp
    rec["rules"] = check_response(inp, text)
    rec["material_ok"] = material_coverage(inp, text)     # None = 말해야 할 재료가 없는 행(분모에서 뺀다)
    return rec
