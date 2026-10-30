"""I7 실패 사례 읽기 통신 컴포넌트(보내는 쪽). ops-feedback의 읽기 SDK/HTTP 클라이언트로 확정된(CONFIRMED) 사례와 비식별 trace를 읽는다.

입력 FailureReadRequest {limit}(store는 wiring이 주입), 출력 계약 FailureCase 목록(정답 있는 것만). trace는 get_trace(ref)로 한 건씩 읽는다."""
from __future__ import annotations

from openit_link.clients import HttpFailureStore


def connect(**kw):
    """원격 failure-store 클라이언트(HttpFailureStore)."""
    return HttpFailureStore(**kw)


def list_confirmed(store, *, limit: int | None = None) -> list[dict]:
    out, cur = [], None
    while True:
        items, cur = store.list_cases(review_status="CONFIRMED", latest_only=True, cursor=cur, limit=100)
        out += items
        if not cur or (limit and len(out) >= limit):
            break
    out = [c for c in out if c.get("expected") is not None]
    return out[:limit] if limit else out


def get_trace(store, ref):
    return store.get_trace(ref)
