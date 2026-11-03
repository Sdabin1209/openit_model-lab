# 0034. RUNTIME 조항 준수: 서비스 출력, 출구, 판정 조회, 환경

- 상태: 채택됨 (계약 boundary-contracts v3.4.6, RUNTIME.md, 2026-11-04 코드 단계)

## 맥락
계약이 영역 게이트웨이(CONVENTIONS 13절), 공통 환경, 실패 상황의 출력(RUNTIME R3), 공통 기본 규칙(R10)을 정했다. 이 영역의 출력하는 쪽은 model-registry 서비스(I3 서버)와 파이프라인(영역 밖 호출)이다.

## 결정
| 조항 | 결정 | 코드 | 시험 |
|---|---|---|---|
| R5.1 | `/healthz`·`/readyz` 본문이 HealthStatus. 준비 아님은 `not_ready`(옛 `not-ready` 폐기) | `link_registry_serve.make_app` | `tests/test_runtime_registry.py::test_healthz_and_readyz_bodies_are_health_status`, `::test_readyz_503_body_says_not_ready_with_the_new_spelling` |
| R10.11 | `GET /metrics`: Prometheus 텍스트, 인증 없음, 저장소 장애 중에도 200(프로세스 안의 값만). 노출 선언에서 `denied` | 같은 파일 | `::test_metrics_is_prometheus_text_without_auth_and_survives_storage_outage`, `tests/test_exposure.py` |
| R3.10, R4.1 | 저장소 쓰기·읽기 실패(디스크 가득, I/O 오류)는 503 `UNAVAILABLE`이고 `/readyz` 503. 복구되면 재시작 없이 정상 | 서비스 겉옷이 내부 예외를 503으로 | `::test_storage_write_failure_is_503_unavailable_then_recovers_without_restart`, `::test_storage_read_failure_is_503_and_readyz_503_then_recovers` |
| R10.8 | 게시는 전부 저장되거나 아무것도 저장되지 않는다: 매니페스트가 마지막 쓰기, 그 앞 쓰기가 실패하면 이번 호출이 만든 것(어댑터 폴더, 평가 리포트, ID 목록, 버전 순서, 게시자 기록)을 되돌린다. 같은 내용 200, 다르면 409 | `model_registry.FileModelRegistry._s_commit` | `::test_failed_publish_leaves_no_readable_trace`(5곳에서 실패 주입), `::test_republish_same_content_is_200_and_different_is_409_over_http` |
| R10.13, Exposure.max_body_bytes | 게시 본문 상한(노출 선언의 `max_body_bytes`도 같은 값 4294967296) `MODEL_REGISTRY_MAX_BODY` 기본 4GiB: 넘으면 413, 계약 클라이언트 `INVALID_RECORD`, 재시도 없음. 값의 근거와 측정 여부는 [0007](0007-디스크-가드와-서버-한도의-값.md): 어댑터는 LoRA라 작다는 가정의 여유값, **측정하지 않았다**. 8GiB(`max_tar_bytes`) 이하이고 가장 큰 패키지 이상이어야 하는 조건은 실제 8B 어댑터(서버 이전 학습 기록 runs/a_8b_v2 전체가 체크포인트 포함 1.2GB였다. 측정은 한 번뿐)로 충족한다 | 서버 설정 | `::test_publish_body_over_the_limit_is_413_invalid_record_and_not_retried` |
| R10.5 | 로그 쓰기 실패가 요청을 실패시키지 않는다 | `Service.log` 감쌈 | `tests/test_service.py::test_log_write_failure_does_not_fail_the_request` |
| R1.8 | 영역 밖 호출은 `OPENIT_EGRESS_PROXY`(자기 영역 게이트웨이 출구)로. 계약 클라이언트를 만드는 모든 곳이 `egress_proxy`를 넘긴다. 403이면 `PERMISSION_DENIED(EGRESS_DENIED)` | `link_egress`, `wiring/factories.py` | `tests/test_egress_r18.py` |
| R9.1 | 자식 프로세스(ssh, rsync, 학습)에 상속 환경을 통째로 넘기지 않는다: 기본 허용 `PATH HOME LANG LC_ALL SSH_AUTH_SOCK` + 설정 `server.pass_env` | `server_train_wrap.child_env` | `tests/test_train_ssh.py::test_child_processes_get_only_the_allowed_environment` |
| 노출 선언 | `egress`: dataset-store, question-authoring, ops-feedback, release-gate (INTERFACES 표에서 model-lab이 읽는 I2·I1·I7·I4). 추론 서버(I9)는 HTTP로 부르지 않는다(3.4.6) | `deploy-exposure.json` | `tests/test_exposure.py` |
| I4 판정 조회 | 게시한 패키지(lora_a, lora_b)에 대한 `find_decisions`를 run 기록 `decisions`에 넣는다. 조회 실패는 실행을 실패시키지 않는다. 후보는 제출하지 않는다 | `link_release_read`, `Run.record_decisions` | `::test_record_decisions_*`, `::test_decisions_status_*` |
| lineage.question_sets | 게시할 때 반드시 채운다(1개 이상). 비면 게시하지 않는다 | `package_build.training_question_sets` | `::test_manifest_lineage_question_sets_is_filled_and_empty_is_refused` |

## 왜
계약 조항과 표준(RFC 9110, Prometheus 텍스트 형식, 12-Factor XI). 조항에 없는 덧대기(재시도 루프, 예외표)는 넣지 않았다. 원자성은 이 파일 저장소가 DB 트랜잭션이 없어 "마지막에 매니페스트 쓰기 + 앞 쓰기 되돌리기"로 구현한다. 되돌리기도 실패하면(디스크 문제) 남은 흔적은 서비스 시작 때 recover가 지우고, 매니페스트가 없어 읽히지 않는다.

## 검토한 대안
- DB(SQLite/PostgreSQL)로 게시를 트랜잭션화: 저장소 종류 변경이라 [0001](0001-직접-만든-모델-레지스트리.md) [0002](0002-파일시스템과-JSON-메타-저장.md)를 대체해야 하는 큰 일이라 이번 범위 밖.
- 게시 재시도 루프를 서버에 넣기: 조항은 호출자가 같은 요청을 다시 보내는 것(R10.8)이다.

## 결과와 한계
얻는 것: 조항별 상태+본문 시험과 변이 확인(되돌리기 제거, 환경 통째 전달, `not_ready` 표기, `/metrics` 경로 제거 각각이 시험을 실패시킴). 한계: 되돌리기의 되돌리기 실패 경로는 시험하지 못했다. 파이프라인의 복구는 호출자(학습 서버)의 재실행이다. `io.openit.jobs` 라벨: 이 이미지는 레지스트리 서비스뿐이고 파이프라인(`run-once`)은 이미지가 아니라 학습 서버의 가상환경에서 돈다(D22)라 deploy가 띄우는 잡이 없어 선언하지 않았다(`JobsDeclaration.jobs`는 1개 이상이어야 한다).

## 되돌리는 조건
계약 RUNTIME 조항이 바뀌거나 저장소 종류를 바꾸면 새 ADR로 대체한다.
