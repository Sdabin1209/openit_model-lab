"""LoRA-B 응답 문장의 규칙 검사(2.0.0). 기계로 확인할 수 있는 것만 본다. 사람 검수를 대신하지 않는다.

항목은 학습 서버의 B 평가(`~/myungchan/eval_lora_b_v2.py`)가 보는 것(끝맺음, 다음 질문, 새 숫자, 금지 표현, 문장 수, JSON 출력)과
같은 뜻이며, 구현은 이 리포지토리의 것이다(코드를 가져오지 않았다). 금지 표현 목록과 문장 수 범위는 그 평가의 값을 따른 잠정값이고
확정은 도메인 소유자 몫이다(미정)."""
from __future__ import annotations

import re
from typing import Mapping

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
