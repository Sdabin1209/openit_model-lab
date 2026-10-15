[처리됨 2026-10-15: 클라이언트가 v1.2.0에 있다. 대역을 지우고 실제 `HttpModelRegistry`로 서버를 시험한다.]

# CR-L005 HTTP 클라이언트 패키지와 서비스 이름 확인 (수신: 헤드)

- `boundary_contracts.clients`(HttpModelRegistry 등)는 현재 체크아웃(v1.1.0 + 문서 커밋)에 없다. v1.1.1이 나오면 `requirements*.txt`·`requirements.lock`의 태그를 올리고, 설정의 `registry.kind: http`가 그 클래스를 쓴다. 그 전에는 `tests/doubles/http_clients.py`의 대역(같은 Protocol)으로 서버를 시험했다. 대역과 실제 클라이언트가 같은 적합성 시험(`boundary-contracts/conformance/`)을 통과하는지는 클라이언트가 나온 뒤 확인한다 [미확인].
- 서버 확인 요청: (1) 인증 실패(토큰 없음/틀림)에 401이 아니라 403을 낸다(CONVENTIONS 9절 표에 401이 없어서). (2) `GET /v1/packages/{id}/versions`도 `cursor`/`limit` 목록 형식을 따른다. (3) `/v1/eval-reports/{ref}`는 `application/octet-stream`. 다르게 기대하면 알려 달라.
- 토큰 이름은 `MODEL_REGISTRY_READ_TOKENS`/`MODEL_REGISTRY_WRITE_TOKENS`(지시대로). 읽기 역할 이름은 임의(예: `release-gate`)이고 서버는 역할을 구분하지 않고 "읽기 가능"으로만 쓴다.
