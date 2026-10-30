# 시험 중 시간 (U422)

규칙: 시험 중 실제 시간을 기다리거나 재서 판정하는 곳이 0이다. 시계는 주입하고(ADR 0029), 라이브러리(waitress·신호·link)의 시간 동작은 시험하지 않고 넘긴 값과 우리 처리만 본다.
남은 숫자는 `tests/conftest.py`의 `HANG_GUARD_SECONDS = 300`(매달림 감시, 판정 아님)과 그 배수 `EXTERNAL_TOOL_GUARD_SECONDS`(docker·pip 외부 도구)뿐이다.
정적 점검: `grep -n "time\.monotonic\|perf_counter\|time\.time\|time\.sleep\|sleep(\|settimeout\|wait(\|join(\|timeout=" tests/*.py` 의 모든 줄이 아래 표에 있다.

## 1. 시간에 기대던 시험 (헤드 목록 `u422-timing-tests.txt` 5건 + 이 영역에서 찾은 나머지)

| 시험 | 목적 한 줄 | 처리 | 바뀐/대체 시험 |
|---|---|---|---|
| test_hardening::test_sigterm_stops_cleanly_and_removes_tmp | 종료하면 임시 폴더를 지우고 정상 종료한다 | 2 라이브러리/신호 -> 우리 처리 확인 | test_server_config::test_main_passes_environment_values_to_the_service_and_server (main이 끝나면 서버를 닫고 `.tmp`를 지움) |
| test_hardening::test_sigterm_with_idle_keepalive_connection_exits_fast | 유휴 연결이 있어도 종료가 소켓 timeout을 기다리지 않는다 | 1 우리 코드(drain) + 2 | test_server_config::test_drain_closes_idle_connections_and_stops_accepting |
| test_hardening::test_drain_lets_in_flight_request_finish | 진행 중 요청은 끝까지 처리하고 종료한다 | 1 시계 주입 | test_server_config::test_drain_waits_for_a_request_in_flight_then_closes_it, ::test_drain_cuts_connections_still_busy_when_the_grace_is_over (가짜 채널, 가짜 시계가 유예 만료를 일으킴) |
| test_server_properties::test_a_slow_request_does_not_block_a_concurrent_one | 느린 요청이 다른 요청을 막지 않는다 | 2 라이브러리(waitress 스레드) | test_server_config::test_make_server_passes_the_configured_limits_to_waitress (threads를 넘겼는지) |
| test_server_properties::test_sigterm_lets_an_upload_in_progress_finish | 업로드 중 종료 신호에도 요청을 끝내고 종료 | 2 신호/라이브러리 | 위 main 시험(신호 처리기가 `shutdown_grace`로 drain을 부름) + drain 시험 |
| test_server_properties::test_clients_that_stall_mid_request_do_not_block_others | 요청을 멈춘 클라이언트가 다른 클라이언트를 막지 않는다 | 2 라이브러리 | 지움(threads 값 확인으로 대체) |
| test_server_properties::test_stalled_connection_is_closed_after_the_timeout | 멈춘 연결을 timeout 뒤 서버가 닫는다 | 2 라이브러리 | 지움. test_make_server_passes_...가 channel_timeout·cleanup_interval 값을 확인 |
| test_server_properties::test_declared_body_over_the_limit_is_refused_without_receiving_it | 상한을 넘는 본문을 받지 않고 거절한다 | 2 라이브러리(waitress·link) | 지움. test_routes_declare_streaming_publish_with_the_body_limit_and_roles (max_body·stream_body 선언)와 main 시험(max_body가 환경에서 옴) |
| test_server_properties::test_client_reads_over_the_limit_413_as_invalid_record_not_retryable | 계약 클라이언트가 413을 INVALID_RECORD로 읽는다 | 2 계약 클라이언트의 몫(TCP 경쟁 포함) | 지움 |
| test_hardening::test_connection_cap_queues_extra_connections_and_idle_connection_times_out | 연결 상한과 유휴 timeout | 2 라이브러리 | 지움. connection_limit = max_connections + 2를 test_make_server_passes_...가 확인 |
| test_hardening::test_repeated_bad_tokens_are_throttled_but_valid_tokens_always_pass | 틀린 토큰 반복은 제한하고 정상 토큰은 통과 | 1 시계 주입 | 같은 이름으로 test_server_config에 다시 씀(가짜 시계, WSGI 직접 호출, 소켓·sleep 없음) |
| test_hardening::test_per_request_state_is_reset_on_a_reused_connection | 재사용 연결에서 거절이 실제 오류를 돌려준다 | 2 라이브러리(waitress keep-alive, 큰 본문 소켓) | 지움. 경로별 역할은 test_service::test_tokens_are_separate |
| test_service::test_rejected_upload_still_returns_real_error_not_connection_reset | 거절된 큰 업로드가 연결 초기화 대신 실제 오류를 본다 | 2 라이브러리(TCP 경쟁, 40MiB) | 지움. 우리 거절은 test_tokens_are_separate, test_unsafe_tar_rejected |
| test_service::test_rejection_does_not_read_a_huge_body_and_closes_the_connection | 상한 초과 본문을 읽지 않고 거절 | 2 라이브러리 | 지움(위 routes·main 시험) |
| test_auth_401.py (소켓 `settimeout`) | 401/403 응답 줄과 머리글 | 매달림 감시 | settimeout(HANG_GUARD_SECONDS), 판정은 상태 줄 |
| test_build_image.py:55, test_setup_env.py:11,28 | 빌드·설치·수집 프로세스 | 매달림 감시 | `EXTERNAL_TOOL_GUARD_SECONDS`(= HANG_GUARD x 12 = 3600초): docker·pip는 정상이 수십~백 초라 300초는 정상의 수 배에 그쳐 거짓 실패 위험이 있어 같은 상수의 배수로 둔다. 판정은 종료 코드·라벨 |
| test_pipeline.py:290 | 원격 레지스트리 주소 `127.0.0.1:9`(즉시 연결 거부)에서 종료 코드 3 | 시간 판정 아님 | 설정의 `"timeout": 2`를 지움(시한이 결과를 바꾸지 못한다) |
| test_metrics_v2.py:206,216 | 설정의 `"timeout": 30`이 클라이언트 인자로 그대로 넘어간다 | 2(a) 넘긴 값 확인 | 변경 없음(시간을 재지 않는다) |
| test_server_config.py (`ev.wait(HANG_GUARD_SECONDS)`) | 종료 신호 처리기가 drain 스레드를 시작 | 매달림 감시 | 정상은 즉시 |
| test_server_config.py (`FakeClock`, `clock.sleep(61.0)`, `drain(srv, 8.0)`) | 실패 제한 창과 drain 유예 | 1 주입 시계 | 가짜 시계는 시험이 움직일 때만 흐른다 |

## 2. 동작 코드 변경 (시계 주입)
`link_registry_serve`의 `Service`가 `clock`(단조 시계)과 `utc_now`를 주입받고 `drain`이 `clock`·`sleep`을 주입받는다. 진입점 `service/app.py`가 실제 `time`을 넣는다. 컴포넌트는 `time`을 import하지 않는다. 와이어 형식·HTTP 응답은 그대로이고 기존 `test_service`, `test_hardening`, `test_exposure`, `test_auth_401`이 통과한다. ADR 0029.

## 3. smoke·E2E로 넘기는 것 (헤드 판단)
실제 waitress를 띄워 유휴 연결이 닫히는지, 종료 신호 아래 진행 중 업로드가 끝나는지, 연결 상한 대기는 `scripts/smoke_registry.sh`(기동·왕복)에도 없다. 필요하면 smoke에 SIGTERM 한 번을 더하는 일이다(이번에 하지 않았다).
