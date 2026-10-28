# 0023. 레지스트리 서버를 WSGI 앱 + waitress로 바꾼다

## 상태
채택 (2026-10-29, 헤드 지시 단위 4a, 결정 D111). [ADR 0010](0010-서버-구현은-표준-라이브러리-HTTP.md)(표준 라이브러리 `http.server`)를 대체한다(Supersedes 0010). ADR 0007의 연결·timeout·종료·본문 버리기 값 중 서버 설정이 된 것도 이 문서가 새 출처다.

## 맥락
`service/app.py`는 `ThreadingHTTPServer` 위에 연결 상한 세마포어, 소켓 timeout, 유휴 keep-alive 추적과 우아한 종료, 거절 때 본문 버리기를 직접 만들어 얹었다(표준성 점검 H2, 분류 O: 재발명). 지시(D111)는 이런 서버 운영 기능을 검증된 WSGI 서버의 설정으로 얻고 직접 코드를 지우는 것이다. 이 서버는 단일 프로세스·다중 스레드로 디렉터리 잠금(`registry.py`)과 메모리 안의 상태(인증 실패 제한, 게시 슬롯)를 쓴다.

## 결정
- 앱은 WSGI(PEP 3333) 호출 가능 객체(`make_app`)이고, 서버는 `waitress.create_server`다. 의존은 `waitress==3.0.2`(잠금 파일, Dockerfile).
- 이전 성질을 서버 설정으로 옮겼다. 값은 모두 **측정하지 않았다**.

| 성질 | waitress 설정 | 값 | 출처 |
| --- | --- | --- | --- |
| 유휴·미완성 요청 연결 닫기 | `channel_timeout`, `cleanup_interval` | 30초, 5초 | 30은 이전 소켓 timeout 값을 그대로 옮김(측정 안 함, ADR 0007). 5는 점검 주기: timeout을 최대 5초 넘겨 닫는다(waitress 기본 30). 측정 안 함 |
| 동시 연결 상한 | `connection_limit` | 32 + 2 | 32는 이전 값 그대로(측정 안 함). +2는 waitress가 리스너와 내부 트리거를 연결 수에 세기 때문이다(`max_connections=1`로 하나도 못 받는 것을 시험으로 확인) |
| 작업 스레드 | `threads` | 16 | waitress 기본은 4. 느린 다운로드가 스레드를 잡아도 `/healthz`가 막히지 않게 여유를 둔 값. 측정 안 함 |
| 본문 상한 | `max_request_body_size` | `MODEL_REGISTRY_MAX_BODY`(4GiB) | 이전 값 그대로(근거 없음, 측정 안 함, ADR 0007). 넘으면 waitress가 413을 낸다 |
| 우아한 종료 | 없음(waitress에 기능이 없다) | 유예 8초 | 서버 채널 목록으로 `drain()`(아래). 8초는 Docker 기본 정지 유예 10초보다 짧게(ADR 0007 그대로) |

- 인증, 인증 실패 제한, 동시 게시 1건, 디스크 여유 검사는 서버가 아니라 앱의 규칙이라 그대로 앱에 둔다.
- 지운 직접 코드: `ThreadingHTTPServer` 하위 클래스(연결 세마포어, 추적, `process_request`), `BaseHTTPRequestHandler` 하위 클래스, `_drain`(거절 때 본문 버리기), `DRAIN_CAP`, `DRAIN_IDLE`, 소켓 timeout 설정, 본문을 다시 파일로 복사하던 루프(`tarutil.unpack`이 서버가 받아 둔 본문을 바로 푼다).
- 남긴 것: `drain()`(약 25줄). waitress는 SIGINT에서 작업 스레드만 멈추고, 아직 보내지 못한 응답을 비우지 못한다. 그래서 새 연결을 받지 않고, 유휴 채널은 보낼 것을 보낸 뒤 닫고, 요청이 진행 중이거나 받는 중인 채널은 유예까지 기다린다. waitress 채널의 공개 속성(`requests`, `request`, `close_when_flushed`, `will_close`)을 쓴다.

## 왜
- 표준: PEP 3333(WSGI). 스레드 수와 연결 수를 서버가 소유하고 앱은 요청 처리만 한다.
- 단일 프로세스 + 스레드 설계(프로세스 안의 잠금과 카운터)라 prefork 서버(gunicorn 기본)가 아니라 waitress다(지시). 순수 Python이라 이미지에 컴파일러가 필요 없다.
- 느린 클라이언트(slowloris)는 waitress가 비동기 I/O로 받으므로 스레드를 잡지 못한다. 이전에는 스레드마다 소켓 timeout에 의존했다. `tests/test_server_properties.py`가 이 성질을 고정한다.

## 검토한 대안과 버리는 이유
- 현상 유지: 직접 만든 운영 기능이 크고 점검에서 재발명으로 분류됐다.
- gunicorn gthread: 본문을 스트리밍하지만 프로세스 모델과 설정이 더 크고, 지시가 waitress다.
- uvicorn(ASGI): 앱을 async로 다시 써야 한다.

## 결과 (동작이 바뀐 곳. 지시는 동작을 바꾸지 않는 것이었으나 아래는 waitress로는 얻을 수 없었다)
1. **본문을 다 받은 뒤에 앱이 불린다.** 이전에는 토큰이 없거나 틀리면(401), 권한이 없으면(403) 본문을 읽기 전에 답했다. 지금은 인증되지 않은 호출자도 `max_request_body_size`까지 본문을 보낼 수 있고 서버는 그것을 임시 파일(`TMPDIR`, 512KiB를 넘으면)에 둔 뒤 401/403을 낸다. **디스크를 쓰는 비인증 경로가 생긴 것이다.** 완화: 연결 상한 32, `channel_timeout`, 본문 상한. 근본 해결은 서버 앞(프록시)에서 본문 상한을 낮추거나 인증하는 것이다. 어댑터 크기 실측으로 `MAX_BODY`를 낮추는 것을 권한다(헤드 결정 필요). 이전 시험 `test_rejection_does_not_read_a_huge_body...`의 403은 상한 초과 선언의 413으로 바뀌었다.
2. **상한 초과 본문의 응답이 JSON이 아니다.** waitress가 413을 자기 본문으로 낸다(이전: 400 `INVALID_RECORD` JSON). 계약 v2.2.2의 클라이언트가 413/414/431을 `INVALID_RECORD`(재시도 안 함)로 읽는다(CONVENTIONS 9절; v2.2.1에서는 `UNAVAILABLE`로 읽는 것을 실행해 확인했다). 시험: `test_client_reads_over_the_limit_413_as_invalid_record_not_retryable`.
3. **연결 상한을 넘은 연결은 503을 받지 않고 backlog에서 기다린다**(waitress는 accept를 멈춘다). 503 `UNAVAILABLE`은 동시 게시 초과와 인증 실패 제한에만 남았다.
4. **종료 중 진행 요청의 응답에 `Connection: close`가 붙는지 확인하지 않는다.** 응답을 보낸 뒤 서버가 연결을 닫는다.
5. 얻는 것: 느린 클라이언트가 스레드를 잡지 않는다. 직접 코드 약 200줄이 줄었다.

## 되돌리는 조건
비인증 본문 수신(결과 1)이 허용되지 않는다고 헤드가 판단하면, 서버 앞에 본문 상한·인증을 두거나 본문 전에 거절할 수 있는 서버로 바꾼다. 측정으로 스레드·연결 값을 다시 정한다.
