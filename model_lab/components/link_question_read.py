"""question-authoring I1 문항 정의 읽기 통신 컴포넌트(읽기 전용, D13). 입력: 평가 샘플이 속한 문항 세트 참조 {id, version[, content_hash]},
출력: 문항 카탈로그 {(세트 id, 버전): {문항 id: {question_type, unit, range}}}. 평가를 문항 유형·단위·범위별로 쪼개는 데만 쓴다(브리프 4절).
문항 정의를 쓰는 권한은 없다(저작 API만 쓴다, INTERFACES I1)."""
from __future__ import annotations

from openit_link.clients import HttpQuestionReader


def connect(**kw):
    return HttpQuestionReader(**kw)


def _ref(reader, qs: dict) -> dict:
    """해시가 참조에 없으면 I1 list_versions 에서 같은 버전의 참조를 찾는다(version 은 문항 세트 번호 n 의 문자열, I1)."""
    if qs.get("content_hash"):
        return {"id": qs["id"], "version": str(qs["version"]), "content_hash": qs["content_hash"]}
    cursor = None
    while True:
        items, cursor = reader.list_versions(qs["id"], cursor=cursor, limit=100)
        for r in items:
            if str(r["version"]) == str(qs["version"]):
                return r
        if not cursor:
            raise KeyError(f"{qs['id']}@{qs['version']}")      # 호출부가 NOT_FOUND 와 같이 다룬다


def describe(bank: dict) -> dict:
    """QuestionBank -> {문항 id: {question_type, unit, range}}. 유형은 칸의 data_type, 단위는 칸의 unit 과 units[].name, 범위는 minimum/maximum 이 있는지."""
    out = {}
    for q in bank["questions"]:
        types, units, bounded = set(), set(), False
        for s in q["slots"]:
            if s.get("data_type"):
                types.add(s["data_type"])
            if s.get("unit"):
                units.add(s["unit"])
            units |= {u["name"] for u in s.get("units") or [] if u.get("name")}
            bounded = bounded or any(k in s and s[k] is not None for k in ("minimum", "maximum", "exclusive_minimum"))
        out[q["question_id"]] = {"question_type": sorted(types), "unit": sorted(units), "range": "bounded" if bounded else "unbounded"}
    return out


def catalog_for(reader, question_sets: list[dict]) -> dict:
    cat: dict = {}
    for qs in question_sets:
        key = (qs["id"], qs["version"])
        if key not in cat:
            cat[key] = describe(reader.get_question_bank(_ref(reader, qs)))
    return cat
