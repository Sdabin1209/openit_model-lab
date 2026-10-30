# CR-L013: link v0.1.0 App으로 model-registry 받는 쪽을 옮기려면 필요한 것 (헤드에게)

- 확인한 것(`link/openit_link/server.py` 읽음): `App._read`는 `environ["wsgi.input"].read(n)`로 본문 전체를 메모리에 읽고, `Route.max_body`로 413을 낸다.
- model-registry의 게시(`POST`)는 어댑터 tar를 최대 4GiB(`MODEL_REGISTRY_MAX_BODY`)까지 디스크로 스트리밍해 받고(ADR 0023), 동시 게시 상한 초과 503, 같은 주소의 인증 실패 제한 503, 여유 공간 부족 503, 정지 시 진행 중 요청 마무리(drain)를 한다. 시험 `tests/test_server_properties.py`, `tests/test_hardening.py`가 이 동작을 고정한다. 동작은 오늘 바꾸지 않는다.
- 부족한 것(제안): (1) `Route(stream_body=True)`: 본문을 읽지 않고 `wsgi.input`을 처리 함수에 넘기는 모드, (2) 인증 실패 제한과 요청 전 점검 훅(`before_handler`), (3) 처리 함수가 낸 파일 응답(`Response(file_path=...)`).
- 현재 상태: 보내는 쪽(`link_dataset_fetch`, `link_failure_read`, `link_registry_publish`)은 `openit_link.clients`를 쓴다. 받는 쪽 `link_registry_serve`는 위 기능이 생기기 전까지 자체 WSGI 앱이다(401/403/BoundaryError 규약 구현이 영역 코드에 남아 있음 = 이 CR이 해소되면 지운다).
