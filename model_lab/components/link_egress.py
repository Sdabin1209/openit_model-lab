"""영역 게이트웨이 출구 통신 컴포넌트(RUNTIME R1.8, CONVENTIONS 13절). 입력: 환경(매핑), 출력: 출구 주소 또는 None.
환경을 통째로 쓰지 않고 공통 이름 OPENIT_EGRESS_PROXY 하나만 꺼내 검증한다(R9.1). loopback http가 아니면 ValueError."""
from __future__ import annotations

from typing import Mapping, Optional

from boundary_contracts.clients import egress_proxy_from


def egress_proxy(env: Mapping[str, str]) -> Optional[str]:
    return egress_proxy_from(env)
