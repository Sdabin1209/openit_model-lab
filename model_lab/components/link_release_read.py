"""release-gate I4 판정 조회 통신 컴포넌트(읽기 전용). 입력: 게시한 패키지 {역할: ArtifactRef}, 출력: 판정 요약 목록.
model-lab은 후보를 제출하지 않는다(제출 권한 없음, INTERFACES I5)."""
from __future__ import annotations

from openit_link.clients import HttpReleaseReader


def connect(**kw):
    return HttpReleaseReader(**kw)


def decisions_for(reader, packages: dict) -> list[dict]:
    """각 역할의 패키지(lora_a/lora_b)와 맞는 판정을 모두 모은다(최신부터, 같은 decision_id는 한 번)."""
    seen: dict = {}
    for role, key in (("LORA_A", "lora_a"), ("LORA_B", "lora_b")):
        p = packages.get(role)
        if not p:
            continue
        ref = {"id": p["package_id"], "version": p["version"], "content_hash": p["content_hash"]}
        cursor = None
        while True:
            items, cursor = reader.find_decisions(**{key: ref}, cursor=cursor, limit=100)
            for d in items:
                seen.setdefault(d["decision_id"], {"decision_id": d["decision_id"], "result": d["result"], "decided_at": d["decided_at"],
                                                   "matches": [], **({"release_id": d["release_id"]} if d.get("release_id") else {})})["matches"].append(role)
            if not cursor:
                break
    return list(seen.values())
