"""설정의 HttpClientSettings로 이웃 영역의 HTTP 클라이언트를 만든다(계약 4.0.0 D135: 설정에 구현 모듈 경로를 두지 않는다).

역할(설정 키)이 클라이언트를 정하고 `kind: http`는 전송 방식만 고른다. 서비스 토큰은 `token_secret`이 가리키는 이름으로
`boundary_contracts.read_secret(이름)`이 읽는다(`<이름>_FILE` 파일이 우선, D84. 비밀 값은 설정 파일에 두지 않는다).
시험 대역은 설정이 아니라 `Run(dataset_store=..., failure_store=...)` 같은 배선 인자로 주입한다(제품 설정에 시험용 문을 두지 않는다)."""
from __future__ import annotations

import os
from typing import Mapping

from boundary_contracts import SecretError, read_secret

from ..components import link_dataset_fetch, link_failure_read, link_egress, link_registry_publish, link_release_read
from .errors import ConfigError

# 왜 역할 이름으로 고르나: 설정 키(역할)가 클라이언트를 정한다(HttpClientSettings 설명, 구조 규칙 1). link_* 컴포넌트만 HTTP 클라이언트를 만든다(DESIGN 6절 3항)
_LINK = {"model_registry": link_registry_publish, "dataset_store": link_dataset_fetch,
         "failure_store": link_failure_read, "release_reader": link_release_read}


def build_http(role: str, settings: Mapping, env: Mapping = os.environ):
    """settings = 계약 HttpClientSettings {kind: http, base_url, token_secret, timeout_seconds?}."""
    try:   # NAME_FILE(파일 경로)이 있으면 그 내용, 없으면 NAME 환경 변수(D84). 값은 오류에 담기지 않는다
        token = read_secret(settings["token_secret"], environ=env, required=True)
    except SecretError as e:
        raise ConfigError(str(e)) from None
    kw = {"base_url": settings["base_url"], "token": token}
    if "timeout_seconds" in settings:
        kw["timeout"] = settings["timeout_seconds"]       # 없으면 계약 클라이언트 기본값(RUNTIME R10.3)
    # RUNTIME R1.8, CONVENTIONS 13절: 영역 밖 호출은 자기 영역 게이트웨이의 출구(OPENIT_EGRESS_PROXY)로만 나간다. 환경을 통째로 넘기지 않고 이 이름 하나만 꺼낸다(R9.1)
    return _LINK[role].connect(**kw, egress_proxy=link_egress.egress_proxy(env))
