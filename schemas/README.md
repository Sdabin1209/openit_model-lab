# 영역 스키마 (D117)

`schemas/<타입>.schema.json`은 model-lab 영역 내부 타입의 JSON Schema(draft 2020-12)다. 파일 이름이 타입 이름이다. 예시는 `examples/<타입>.valid*.json`, `<타입>.invalid*.json`이고 `tests/test_schemas.py`가 모든 스키마에서 통과·거부 결과를 확인한다. 컴포넌트 표(어느 컴포넌트가 어느 타입을 받고 내는지)는 `docs/components.md`.

- 영역 사이 타입은 복사하지 않는다. 계약 스키마는 파일 이름 그대로 `$ref`한다(예: `ServingConfig.schema.json`). `model_lab/wiring/schemas.py`가 설치된 계약 패키지로 풀고, 이름이 겹치면 시험이 깨진다.
- 학습 서버와의 형식(`ServerDatasetRow`, `ServerTrainInvocation`, `TrainWrapRecord`)은 영역 소유다(D107). 이전 `docs/schemas/`에서 옮겼고 코드가 쓴다(`server_row_convert`가 `ServerDatasetRow`를 검증하고, `tests/test_schemas.py`가 실제 서버 줄을 확인한다).
- 예시 대부분은 실제 파이프라인(dry-run)을 한 번 돌려 얻은 문서다. 거부 예시는 통과 예시에서 필수 키 하나를 지우거나 모르는 키를 넣어 만들었다.
- `types/`는 이 스키마에서 생성한다(README 4절).

## 타입 목록

`AdapterCheckRequest`, `AdapterConfig`, `DatasetFetchRequest`, `DatasetRef`, `DatasetSamplesRequest`, `DatasetSnapshot`, `EvalDetailReport`, `EvalReportRequest`, `EvalSampleRequest`, `EvalServingPlan`, `EvaluationReportRequest`, `FailureReadRequest`, `HfCacheInfo`, `HfCacheQuery`, `InferRequest`, `InferenceResult`, `PackageBuildRequest`, `PackagePublication`, `RegistryServiceConfig`, `RegressionSet`, `RegressionSetRequest`, `RoleSamples`, `RunConfig`, `RunPlan`, `RunRecord`, `SampleRecord`, `SecretFree`, `ServerDatasetRow`, `ServerTrainInvocation`, `TrainDataRequest`, `TrainExample`, `TrainLineage`, `TrainRecordCheckRequest`, `TrainRequest`, `TrainResult`, `TrainWrapConfig`, `TrainWrapRecord`, `UnevaluatedReportRequest` (38개)

## 스키마 밖 조건

코드에 남아 있는 거부 지점 중 스키마로 옮기지 못한 것이다. 목표는 0이었으나 **29건**이 남았다. 이유는 대부분 (가) 문서 밖 자원(파일·해시·GPU·디스크)의 상태, (나) 두 값·두 문서의 대조, (다) 함수 인자라서 JSON Schema가 다루는 범위(한 문서의 형식) 밖이기 때문이다. 이 목록은 코드의 `raise` 전수 조사(2026-10-30)에서 만들었다.

| # | 위치 | 조건 | 스키마로 옮기지 못한 이유 |
|---|---|---|---|
| 1 | config.py `load_train_config` | `server.arg_map`의 키는 표준 인자 이름 또는 `hyperparams`의 키여야 한다 | 다른 속성의 값(키 집합)을 허용 목록으로 쓰는 조건은 JSON Schema로 쓸 수 없다 |
| 2 | config.py `load_train_config` | `server.arg_map`의 값(인자 이름)은 서로 달라야 한다 | 객체 값의 유일성은 표현할 수 없다(`uniqueItems`는 배열만) |
| 3 | config.py `load_train_config` | `base_model.profile`이 가리키는 `profiles/<이름>.yaml`의 기반 모델 id가 `base_model.id`와 같다 | 다른 파일의 내용과의 대조 |
| 4 | config.py `load_config` | 프로파일 파일을 읽어 병합한다. 프로파일이 없으면 거부한다 | 파일 존재와 내용은 문서 밖 자원 |
| 5 | config.py `_read` | 설정 파일이 읽히고 YAML 1.2 코어 스키마로 해석되며 중복 키가 없다(ADR 0015) | 파일·구문 층의 규칙이라 해석 뒤의 문서에는 흔적이 없다 |
| 6 | wiring/pipeline.py `Run` | `evaluation.regression.enabled`이면 `inputs.failure_store`가 있다(또는 주입된 객체가 있다) | 시험·호출부가 객체를 주입할 수 있어 설정 문서만으로 결정되지 않는다 |
| 7 | wiring/pipeline.py `Run.run` | `stop_after`는 단계 이름 여섯 중 하나다 | 함수 인자(설정 문서가 아님). CLI에서는 argparse `choices`가 거른다 |
| 8 | wiring/pipeline.py `stage_load_dataset` | 실행 중에 데이터셋 매니페스트가 달라지지 않았다 | 두 시점의 읽기 결과 대조 |
| 9 | wiring/pipeline.py `stage_evaluate` | 평가 샘플이 1건 이상이다 | 파일에서 읽은 뒤의 개수. 입력 문서가 아니라 중간 산출물 |
| 10 | wiring/settings.py `system_prompt_text` | `system_prompt_file`이 UTF-8로 읽히고 공백뿐이 아니다 | 파일 내용은 문서 밖 자원 |
| 11 | wiring/settings.py `contract_version` | `schema_dir` 상위의 `CONTRACT_VERSION`이 정수로 읽힌다 | 파일 내용 |
| 12 | wiring/factories.py | 팩토리 이름이 허용 목록(ADR 0017)에 있다 | 허용 목록은 코드 상수이고 시험 대역을 포함한다 |
| 13 | wiring/attach_flow.py | `created_at`은 인자 또는 `SOURCE_DATE_EPOCH`(10진 정수, 범위 안)에서만 온다 | 환경 변수 입력. 시각을 지어내지 않는 규칙(ADR 0014, 0020) |
| 14 | wiring/attach_flow.py | 시스템 프롬프트 파일이 읽히고 비어 있지 않다. 프로파일에 기반 모델이 있다 | 파일·프로파일 내용 |
| 15 | components/adapter_check.py | 어댑터 디렉터리에 필수 파일 두 개가 있고 `adapter_config.json`이 객체로 읽힌다 | 파일 존재(자원 상태) |
| 16 | components/adapter_check.py | `adapter_config.base_model_name_or_path` == 요청한 기반 모델 id, `r` <= `max_rank` | 한 문서의 값을 다른 입력의 값과 비교하는 조건 |
| 17 | components/train_record_verify.py | 학습 기록의 `role`·`dataset.id/version/content_hash`·`base_model.id`가 입력과 같다 | 기록과 입력 두 문서의 대조 |
| 18 | components/train_record_verify.py | 기록의 어댑터 파일 해시가 지금 디스크의 파일과 같다 | 해시 계산(자원 상태) |
| 19 | components/dataset_samples.py | 평가 샘플의 `scenario_id`·`sample_id`가 학습·검증과 겹치지 않는다 | 집합 사이 교차 조건 |
| 20 | components/dataset_samples.py | JSON Lines 각 줄이 계약 샘플 스키마를 통과한다 | 줄 단위 적용(스키마는 줄 하나의 문서). 위반 줄 번호만 오류에 싣는다 |
| 21 | components/link_dataset_fetch.py | `content_hash` 재계산 일치, 파일 해시 일치, 문진 스키마 정수 버전 일치 | 해시 계산과 환경값 대조 |
| 22 | components/server_row_convert.py | 한 파일의 시스템 프롬프트가 한 종류다. 줄이 JSON이다. 사용자·응답 문자열이 JSON으로 읽힌다 | 줄 사이 대조와 문자열 안의 JSON |
| 23 | components/train_data.py, server_row_convert.py | `role`은 LORA_A/LORA_B, LoRA-B 정답은 문자열 | 함수 인자 검사. `TrainDataRequest`가 정하지만 변환 함수를 직접 부르는 호출(시험·스크립트)은 스키마를 거치지 않는다 |
| 24 | components/evaluation_report_build.py | 지표 이름이 계약 지표 레지스트리에 있다 | 정본이 계약 패키지의 함수(`metric_name_problems`)다 |
| 25 | components/model_registry.py | 게시 검증: 매니페스트 스키마, `content_hash`·`adapter_hash`·`serving_config_hash` 재계산, 시스템 프롬프트·`ModelPackageLayout`, 같은 버전의 내용 충돌 | 스키마는 계약이 맡고(`validator`), 해시 계산과 저장 상태 대조는 문서 밖이다 |
| 26 | components/model_registry.py, link_registry_serve.py | 경로의 Id 형식, `limit`·`cursor` 값, tar 안의 경로('..', 절대 경로, 심볼릭 링크) | HTTP 경로·쿼리·스트림 입력. `RegistryServiceConfig`는 기동 설정만 다룬다 |
| 27 | service/app.py | 토큰 문자열 형식(`역할=토큰`)과 최소 길이·글자 다양성 | 비밀 값은 스키마에 적지 않는다(D84). 길이 하한 근거는 ADR 0009 |
| 28 | components/server_train_wrap.py | 서버 스크립트·데이터셋 파일 존재, 스크립트 sha256 일치, 디스크 여유, GPU, 스크립트 종료 코드 | 실행 환경 상태 |
| 29 | components/lora_train_hf.py, backend_hf_*.py | 학습 환경(torch 등)과 CUDA GPU가 있다. 최대 길이 안에 드는 샘플이 있다 | 실행 환경 상태와 데이터 길이(토큰화 뒤에야 안다) |

옮긴 것(참고): 학습 기록 형식(`TrainWrapRecord`, 시험의 부분 기록을 완전한 기록으로 고쳤다), 실행 설정 형식과 비밀 키 규칙(`RunConfig`, `SecretFree`), 두 역할 필수(`RunPlan`), 평가 온도 0(`EvalServingPlan`), HTTP 저장소 필수 필드와 레지스트리 종류별 필수 필드(`RunConfig`), 학습 설정 형식(`TrainWrapConfig`), 서버 줄 형식(`ServerDatasetRow`), 학습 요청 형식(`TrainRequest`), 데이터셋 참조 형식(`DatasetRef`), 서빙 설정 형식(계약 `ServingConfig` `$ref`).
