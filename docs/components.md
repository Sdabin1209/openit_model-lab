# 컴포넌트 분해 (D117, 2026-10-30)

정본 규칙은 `head/docs/DESIGN.md`다. 이 문서는 model-lab 영역의 컴포넌트를 입력(계약 v3.1.1과 `docs/brief.md`)에서 위로부터 나눈 결과다. 기존 코드는 정의의 근거가 아니라 빠진 업무 규칙과 거부 조건을 찾는 대조 자료로만 썼다. 입출력 타입은 `schemas/<타입>.schema.json`(영역 내부) 또는 계약 스키마다. 영역 사이 타입은 `schemas/`에 복사하지 않고 `$ref`로 계약을 가리킨다.

## 1. 업무 단계

영역이 받는 것: 데이터셋(I2), 실패 사례(I7), 설정 파일. 영역이 내는 것: 모델 패키지와 평가 리포트(I3, 게시), I3 서버 면. 업무 단계: 데이터셋 읽기 -> 역할별 샘플 분리·검증 -> 학습 예시 변환 -> 학습 -> 모델 호출 -> 채점 -> 보고·지표 -> 회귀 집합 -> 패키지 만들기 -> 평가 리포트 조립 -> 게시. 학습 서버 정본 스크립트를 감싸는 길(`train`)과 어댑터만 올리는 길(`package-adapters`)은 같은 단계의 일부(점검·계보 대조)를 쓴다.

## 2. 컴포넌트 표

괄호의 "주입"은 바깥 자원(저장소 클라이언트, 적재된 모델 세션, 다른 컴포넌트의 함수)을 wiring이 넘긴다는 뜻이다. 컴포넌트끼리는 import하지 않는다.

| 컴포넌트 | 파일 | 입력 타입 | 출력 타입 | 하는 일 |
|---|---|---|---|---|
| 데이터셋 읽기(보내기, I2) | `components/link_dataset_fetch.py` | `DatasetFetchRequest` (store 주입) | `DatasetSnapshot` (계약 `DatasetManifest`) | 버전을 정하고(없으면 최신) 매니페스트·파일을 내려받아 계약 스키마, content_hash, 문진 스키마 정수 버전, 파일 해시를 확인한다. 원격 클라이언트는 `openit_link.clients`로 만든다 |
| 데이터셋 읽기(로컬 파일) | `components/link_dataset_read_file.py` | 데이터셋 루트 폴더 | `DatasetStore` 읽기 SDK 구현 | synthetic-data의 파일 배치를 읽는 독립 읽기 구현(개발용). 읽기만 한다 |
| 실패 사례 읽기(보내기, I7) | `components/link_failure_read.py` | `FailureReadRequest` (store 주입) | 계약 `FailureCase` 목록, trace | 확정(CONFIRMED)된 정답 있는 사례와 비식별 trace를 읽는다 |
| 모델 게시(보내기, I3) | `components/link_registry_publish.py` | `PackagePublication` (registry 주입) | 계약 `ModelPackageManifest` | 원격 model-registry에 패키지를 게시한다(멱등은 서버가 정한다) |
| 모델 레지스트리 서버(받기, I3) | `components/link_registry_serve.py` | HTTP 요청(`HTTP.md` I3, 설정 `RegistryServiceConfig`) | HTTP 응답, `BoundaryError` 본문 | I3 서버 면. 토큰 역할, 본문·여유 공간·동시 게시 보호, tar 묶기·풀기. link v0.2.0이 스트리밍 본문을 지원하지 않아 아직 자체 WSGI 앱이다(`docs/change-requests/CR-L013-link-큰-본문-스트리밍과-서버-보호.md`) |
| 모델 레지스트리 | `components/model_registry.py` | `PackagePublication` | 계약 `ModelPackageManifest` | 불변 패키지 저장소의 규칙(스키마·해시·시스템 프롬프트·ModelPackageLayout 확인, 멱등, CONFLICT). 파일·메모리 구현. 읽기: 매니페스트, 목록, 어댑터, 평가 리포트 |
| 샘플 분리·검증 | `components/dataset_samples.py` | `DatasetSamplesRequest` | `RoleSamples` | 내려받은 파일에서 역할별 TRAIN/VALIDATION/EVAL 샘플을 계약 샘플 스키마로 검증해 읽고, 평가와 학습·검증의 시나리오·샘플 ID 겹침을 막고, 문항 세트가 하나인 파일의 샘플에는 그것을 채운다 |
| 서버 줄 변환 | `components/server_row_convert.py` | `ServerDatasetRow` 또는 계약 샘플 | 계약 샘플 또는 `ServerDatasetRow` | 학습 서버 데이터셋(messages 형식) 줄과 계약 샘플의 양방향 변환 |
| 학습 데이터 변환 | `components/train_data.py` | `TrainDataRequest` | `TrainExample` | 샘플을 서빙과 같은 render_prompt와 정답 글자의 학습 예시로 바꾼다 |
| 학습(모의) | `components/lora_train_dryrun.py` | `TrainRequest` | `TrainResult` | GPU 없이 결정적으로 어댑터를 만든다(재현성·재개 검증용) |
| 학습(HF) | `components/lora_train_hf.py` | `TrainRequest` (기반 모델 커밋 조회 주입) | `TrainResult` | transformers+peft LoRA 학습, 체크포인트 재개. GPU 필요 |
| 기반 모델 캐시 조회 | `components/hf_cache_lookup.py` | `HfCacheQuery` | `HfCacheInfo` | huggingface_hub 공개 API로 캐시 스냅샷과 커밋을 찾는다 |
| 서버 학습 감싸기 | `components/server_train_wrap.py` | `TrainWrapConfig` | `ServerTrainInvocation`, `TrainWrapRecord`, 점검 보고 | 학습 서버 정본 스크립트의 명령 조립, 실행 전 점검, 실행, 산출물 확인, 기록. `doctor`는 해결 안내를 낸다 |
| 어댑터 점검 | `components/adapter_check.py` | `AdapterCheckRequest` | `AdapterConfig` | 어댑터 디렉터리의 필수 파일, 기반 모델 일치, 순위 상한 |
| 학습 기록 대조 | `components/train_record_verify.py` | `TrainRecordCheckRequest` (기록은 `TrainWrapRecord`) | `TrainLineage` | 학습 기록이 어댑터·데이터셋·기반 모델과 맞는지 보고 계보를 낸다 |
| 모델 호출 | `components/model_infer.py` | `InferRequest` (model-runtime 세션 주입) | `InferenceResult` | 서빙과 같은 경로로 호출한 결과(raw와 보호 적용)를 낸다 |
| 평가 백엔드(오라클) | `components/backend_oracle.py` | 프롬프트 -> 정답 표 | 생성 백엔드 | 평가 경로 시험용으로 정답을 그대로 내는 모의 백엔드(깨진 출력 주입 옵션) |
| 평가 백엔드(HF 어댑터) | `components/backend_hf_adapters.py` | 기반 모델, 어댑터 디렉터리 (라이브러리 적재 주입) | 생성 백엔드 | 어댑터를 얹은 모델로 생성한다. 제약 디코딩 없음. GPU 필요 |
| 평가 백엔드(기반 모델) | `components/backend_hf_base_chat.py` | 기반 모델 (라이브러리 적재 주입) | 생성 백엔드 | 어댑터 없이 기반 모델만으로 생성한다(기반 모델 비교용). GPU 필요 |
| 채점 | `components/eval_sample.py` | `EvalSampleRequest` | `SampleRecord` | 정답과 `InferenceResult`를 비교해 칸별 계수, 위반 코드(계약 `check_action_extraction`), LoRA-B 문장 규칙을 계산한다 |
| 보고·지표 | `components/eval_report.py` | `EvalReportRequest` | `EvalDetailReport`, 계약 지표 목록, 회귀 절 | 채점 기록을 쪼개 보기(문항 세트·본/보지 못한 문항·action·reason)로 집계하고 계약 지표 레지스트리 이름으로 지표를 낸다 |
| 회귀 집합 | `components/regression_set.py` | `RegressionSetRequest` (trace 조회 주입) | `RegressionSet` | 확정 사례를 평가 샘플로 바꾸고 건너뛴 사례를 사유별로 센다 |
| 패키지 만들기 | `components/package_build.py` | `PackageBuildRequest` (사전 점검 주입) | 계약 `ModelPackageManifest` | 어댑터·서빙 설정·계보로 매니페스트를 만든다. 시스템 프롬프트를 어댑터 디렉터리에 넣는다 |
| 평가 리포트 조립 | `components/evaluation_report_build.py` | `EvaluationReportRequest` 또는 `UnevaluatedReportRequest` | 계약 `EvaluationReport` | 지표·패키지·데이터셋 참조로 리포트를 만들고 레지스트리에 없는 지표 이름은 거부한다. 평가하지 않은 길은 n=0 표시 |
| 실행 기록 | `components/run_record.py` | `RunRecord` | `RunRecord` | run.json 원자적 쓰기·읽기, 단계 상태 표시 |

## 3. 컴포넌트가 아닌 것

| 위치 | 하는 일 |
|---|---|
| `model_lab/wiring/pipeline.py` | 단계(`load_dataset -> transform -> train -> evaluate -> package -> publish`)를 순서대로 잇고 컴포넌트 출력을 다음 입력에 넘긴다. 주입(저장소, 학습기, 백엔드, 모델 세션) |
| `model_lab/wiring/evaluation.py`, `regression.py`, `packaging.py`, `train_flow.py`, `attach_flow.py`, `stores.py`, `factories.py` | 컴포넌트 둘 이상을 잇는 연결. 팩토리 허용 목록(ADR 0017) |
| `model_lab/wiring/settings.py`, `schemas.py`, `errors.py` | 설정에서 파생하는 값(경로·해시·역할별 학습 설정), 영역 스키마 적재·검증(규칙 5), 영역 오류 어휘 |
| `model_lab/cli.py`, `config.py`, `__main__.py`, `service/app.py`, `service/__main__.py` | 진입점과 설정 읽기(YAML 1.2 해석, 프로파일 병합). 업무 규칙을 두지 않는다 |
| `model_lab/types/` | `schemas/`에서 생성한 TypedDict. 손으로 고치지 않는다 |

## 4. 의존 방향과 알려진 타협

- 컴포넌트는 `wiring/schemas.py`(스키마 적재·검증)와 `wiring/errors.py`(오류 어휘)만 영역 공용으로 쓴다. 이 둘은 영역이 컴포넌트 사이 스키마를 책임진다(규칙 5)는 뜻의 몫이다.
- `lora_train_hf`와 두 HF 백엔드가 같은 `load_base`를 쓰지만 서로 import하지 않으므로 wiring이 함수를 주입한다(복사하지 않았다).
- `link_registry_serve`는 link v0.2.0의 `App`으로 아직 옮기지 못했다(본문 전체를 메모리에 읽어 4GiB 스트리밍 게시를 못 한다). 변경 요청 CR-L013. 보내는 쪽 세 컴포넌트는 `openit_link.clients`를 쓴다.
