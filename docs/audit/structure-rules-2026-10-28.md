# model-lab 구조 규칙 6개 전수 점검 보고서 (2026-10-28, D104)

기준: `head/docs/audit/structure-rules-audit-spec.md`(R1~R7, 항목 A~G). 영역 약자 ML. 읽기 전용 점검이며 코드·설정·시험은 고치지 않았다.
점검 대상 커밋: `4e0caa3`(작업 트리 변경 없음 확인). 문서 인용은 모두 이 커밋의 파일이다.

## 0. 확인 범위

읽은 것: `CLAUDE.md`, `docs/brief.md`, `README.md`, `docs/boundary.md`, `docs/design.md`, `docs/adr/0004`·`0011`, `model_lab/` 전부(pipeline, evaluate, metrics, metric_names, packager, regression, transform, datasets, schemas, config, factories, attach, train_wrap, registry 일부, service/app.py 전부, backends/fake·hf_base, trainers/base), `configs/train.yaml`·`gpu-dev.yaml`·`profiles/qwen3-8b.yaml`·`gpu-dev-12g.yaml`, `Dockerfile`(라벨·USER·HEALTHCHECK 줄), `../boundary-contracts`의 `INTERFACES.md`(I2·I3·I4·I5·I9), `HTTP.md`(공통·I2·I3·I5), `CONVENTIONS.md`(401/403, 429, readyz), `METRICS.md`, `CHANGELOG.md`, `boundary_contracts/metrics_registry.py`·`metrics.json`.
돌린 것: `.venv/bin/python -m pytest -q -p no:cacheprovider` -> **196 passed**(이 PC의 기존 `.venv` 한 환경). `boundary_contracts.metrics()`로 모델랩이 내는 지표 이름 대조(아래 ML-01).
검색한 패턴: `turn_engine|model_runtime|boundary_contracts|survey_schema` import, `\.\./|/home/|openit|gpu-dev|survey-service|synthetic|ops-feedback|release-gate|question-authoring`, `role ==|LORA_A|LORA_B` 분기, `metric_name_problems`, `submit_candidate|/v1/submissions|GateService|I5`.

## 1. A. 컴포넌트 목록

`docs/design.md` 2절에 모듈별 책임 표가 있으나 컴포넌트마다 입력·출력 스키마를 적은 곳은 없다(brief 3절은 계약 이름만). 아래 표의 스키마 열은 코드에서 읽어 정리한 것이다.

| # | 컴포넌트 | 파일 | 입력 | 출력 | 업무 |
| --- | --- | --- | --- | --- | --- |
| 1 | 설정 | `config.py`(`CONFIG_SCHEMA`) | YAML(+프로파일) | dict(JSON Schema 검증됨), `config_hash` | 설정·프로파일 병합 |
| 2 | 데이터셋 읽기(I2 클라이언트) | `datasets.py`, `factories.py`, `file_dataset_store.py` | I2 `DatasetStore`(`list_versions`,`get_manifest`,`open_file`) | `DatasetManifest`, 파일 | 내려받기·재검증·분할·누수 검사 |
| 3 | 변환 | `transform.py` | `ActionExtractionSample`/`ResponseSample` | `{sample_id,prompt,completion}` JSONL | 학습 예시 만들기 |
| 4 | 학습기(파이프라인용) | `trainers/{base,hf,dryrun}.py` | `TrainRequest`(dataclass) | 어댑터 디렉터리 + dict | LoRA 학습 |
| 5 | 평가 | `evaluate.py`, `text_rules.py`, `backends/` | 샘플, model-runtime 세션 | 기록 dict, 집계 | 서빙 경로로 채점 |
| 6 | 지표/보고서 | `metrics.py`, `metric_names.py` | 평가 기록 dict | `EvaluationReport`(계약 스키마) | 지표 계산 |
| 7 | 회귀 평가 | `regression.py` | I7 `FailureStore` | 회귀 집합·보고서 절 | 실패 사례로 재평가 |
| 8 | 패키저 | `packager.py` | 어댑터 디렉터리, 서빙 설정, 계보 | `ModelPackageManifest` | 매니페스트 |
| 9 | 레지스트리 구현 | `registry.py` | `ModelPackageManifest`, 어댑터, 보고서 바이트 | I3 Protocol | 불변 패키지 저장 |
| 10 | 레지스트리 서버(I3) | `service/{app,tarutil,__main__}.py` | HTTP(HTTP.md I3) | HTTP | I3를 HTTP로 냄 |
| 11 | 파이프라인 | `pipeline.py` | 설정 | `runs/<id>/run.json` 등 | 단계 실행·재개 |
| 12 | 학습 서버 감싸기 | `train_wrap.py` | `train.yaml`(자체 JSON Schema) | `*.train-wrap.json`(record_version 1) | 서버 스크립트 명령 조립·실행·기록 |
| 13 | 어댑터 얹기 | `attach.py` | 어댑터 디렉터리 2개, 프롬프트, 학습 기록 | 패키지 + (게시) | 평가 없이 패키징 |

접착 코드: `cli.py`, `__main__.py`, `errors.py`, `schemas.py`(문진 스키마 검증기와 `CONTRACT_VERSION`), `scripts/*`(smoke, determinism_probe, compare_base_models, check_server_dataset 등).
경계가 없는 덩어리: **`pipeline.py` `Run` 클래스**(417줄, 단계 6개가 한 객체의 메서드와 `self.rec` dict를 공유. ML-03).

## 2. 발견

### P1

**ML-01 | R4·R6·F | P1 | 평가 보고서가 계약 지표 레지스트리에 없는 이름 18개를 필수로 낸다**
- 위치: `model_lab/metric_names.py:12-15`(A_REQUIRED/B_REQUIRED), `model_lab/metrics.py:33-47`(A_RATE_METRICS), `tests/test_evaluation_report.py:47,60`
- 인용: `A_REQUIRED = frozenset({"schema_ok_rate", "action_acc", "action_reason_acc", ...` / `assert A_REQUIRED <= names_a`
- 확인: `boundary_contracts.metrics()`(v2.1.1 설치본, `requirements.txt:2`가 핀한 태그)에 대조하면 `action_acc, action_reason_acc, asks_next_rate, cell_acc, db_row_match_rate, ends_ok_rate, exact_match_rate, json_leak_rate, key_found_rate, next_item_acc, oos_f1, precision_acc, save_acc, schema_ok_rate, sentence_count_violation_rate, status_acc, targets_acc, value_acc`가 없다(ACTIVE인 v2 이름 `action_accuracy` 등과 별개). `metric_name_problems(report)`는 이를 `UNKNOWN`으로 보고한다. 모델랩은 `metric_name_problems`/`metrics()`를 어디서도 부르지 않는다(grep 결과 0건).
- 왜 위반: 지표 이름의 정본은 계약 레지스트리인데(CHANGELOG 2.1.1, METRICS.md) 영역이 자기 이름 목록(`metric_names.py`)을 따로 두고 자기 시험으로 필수화했다. R6(영역 사이 스키마는 헤드 책임)과 R4(지표 이름은 계약이 할당한 값), F(계약 준수)에 어긋난다. 게이트의 `ReviewReport`가 후보와 활성 릴리스를 같은 이름끼리 맞댈 때(HTTP.md I5) 이 이름들은 계약 밖이다. ADR 0011은 "이름은 자유 문자열이라 계약을 깨지 않는다"고 하나 그 뒤 레지스트리가 생겼다.
- 최소 수정: 보고서 생성 직후 `metric_name_problems`를 호출해 시험에서 실패시키고, 레지스트리에 있는 이름만 최상위 `metrics`에 둔다. 보조 이름은 `ext."model-lab".detail`로 옮기거나 헤드에 레지스트리 등록을 변경 요청(`docs/change-requests/`)한다. 폐기 목록 `DEPRECATED_V1`은 레지스트리의 DEPRECATED와 현재 같으나(대조 확인) 복제본이므로 레지스트리를 읽게 바꾼다.

**ML-02 | R1 | P1 | 이웃(synthetic-data)의 저장소 파일 배치를 직접 읽는 구현과 그 이동 절차**
- 위치: `model_lab/file_dataset_store.py:1-3`, `docs/runbook.md:27,37`
- 인용: `레이아웃(synthetic-data boundary.md 1절): <root>/<dataset_id>/<version>/{manifest.json, meta.json, files/<path>}` / runbook 37: `synthetic-data의 출력 폴더(데이터셋 루트)를 rsync -a <루트>/ gpu-dev:openit-gpu/datasets/`
- 왜 위반: 이웃의 저장 구조(`meta.json`의 `seq`까지, 같은 파일 6-10줄)에 기댄다. 공통 규칙 D49("이웃의 저장소 볼륨을 공유하지 않는다")와 R1에 어긋난다. 정본 경로는 I2 HTTP 클라이언트(ADR 0012)로 이미 있다. `gpu-dev.yaml:11` 주석이 이 대체 경로를 안내하고 `configs/gpu-dev-fixture-*`와 시험 대역(`tests/doubles/dataset_store.py`)이 이 레이아웃을 가정한다(후자는 읽었으나 내용 대조는 안 함).
- 최소 수정: 운영·문서에서 `file_dataset_store`와 rsync 절차를 제거하고, 로컬 개발은 I2 메모리 구현(가짜 SDK)이나 `HttpDatasetStore`+참조 서버로 한다. 레이아웃이 필요하면 synthetic-data가 배포한 읽기 SDK를 쓴다.

**ML-03 | R2·R3·R5 | P1 | 파이프라인의 단계들이 서로의 내부 dict·파일 키를 직접 쓴다(컴포넌트 경계 없음)**
- 위치: `model_lab/pipeline.py:361-390`(stage_package), `:341-349`(stage_evaluate), `:335`
- 인용: `metrics=detail.pop("evaluation_report_metrics"), notes=detail.pop("evaluation_report_notes"), detail=detail)` / `"revision": tr["result"].get("base_model_revision")` / `backend=detail["context"]["evaluation_backend"]`
- 왜 위반: 평가 단계가 `report-<ROLE>.json` 파일에 `evaluation_report_metrics`라는 비공식 키를 섞어 쓰고, 패키징 단계가 그 파일을 다시 읽어 키를 `pop`해 `EvaluationReport`로 바꾼다. 학습 단계 결과 dict(`result.resumed_from_step`, `result.base_model_revision`)와 `rec["stages"][...]` 구조를 패키징·게시가 알아야 한다. 단계 사이 스키마가 JSON Schema/dataclass로 없다(`TrainRequest`만 dataclass). 평가->패키징의 I/O는 암묵적 딕셔너리 키 관습(C).
- 최소 수정: 단계 사이 값(평가 결과, 학습 결과, 변환 결과)을 `model_lab/schemas.py` 같은 한 곳에 데이터클래스 또는 JSON Schema로 정의하고, 평가는 완성된 `EvaluationReport` 바이트를 쓰고 패키징은 그것을 그대로 쓴다(보고서 구성 책임을 평가 쪽 한 곳으로).

### P2

**ML-04 | R7·R1 | P2 | LoRA-A 출력 의미 판정을 turn-engine 내부에 직접 결합**
- 위치: `model_lab/evaluate.py:17,146-155`, `model_lab/metrics.py:17,134`, `model_lab/packager.py:83-84`, `model_lab/pipeline.py:37-39`; 시험 `tests/test_metrics_v2.py`, `tests/test_registry.py:7`, `tests/doubles/fixtures.py:13`(turn_engine를 import하는 7개 파일 = 소스 4 + 시험 3)
- 인용: `from turn_engine import DATASET_V2_FLOW, ContractViolation, validate_extraction` / `from turn_engine import validation as _tv` / `for code in _tv.CODES:`
- 구분: packager·pipeline의 2곳은 `ENGINE_VERSION`을 계보에 적는 용도뿐이라 결합이 아니다(계약 `lineage.turn_engine_version`). 결합은 evaluate·metrics 두 곳이다: ① `validate_extraction`을 불러 위반 코드를 세는 것은 METRICS.md `semantic_violation_rate`가 평가의 turn-engine 의존을 허용하므로(METRICS.md:30) 계약에는 맞다. 그러나 R7(ADR 0010)이 정하는 "독립 스키마·기준 검사기" 방향에서는 ② `turn_engine.validation.CODES`(하위 모듈 상수)를 import해 슬라이스 값 집합을 만들고, ③ `DATASET_V2_FLOW`(turn-engine이 "시험용 고정값"이라 밝힌 문항 순서표, `evaluate.py:149`·ADR 0011)를 기본 순서표로 박아 문항 순서의 소유자(문진 스키마)와 어긋날 수 있다. ④ 위반 코드가 turn-engine 버전마다 달라지면 보고서 슬라이스가 바뀐다.
- 최소 수정: 기준 검사기가 독립 패키지/계약으로 올라오면 그것으로 교체. 그 전에는 `_tv.CODES` 대신 `validate_extraction`이 낸 코드만 센다(없는 코드는 0으로 만들지 않는다) 또는 헤드에 공개 API로 `CODES`를 요청.

**ML-05 | R7 | P2 | I2·I7 경계가 보증한 값을 모델랩이 다시 검증**
- 위치: `model_lab/datasets.py:36-53,92-100`, `model_lab/regression.py:74-77`, `model_lab/file_dataset_store.py:4-5`
- 인용: `if canonical.content_hash(manifest) != manifest["content_hash"]:` / `평가 샘플이 학습·검증과 시나리오 또는 샘플 ID를 공유한다` / `if list(validator(_OUT[role]).iter_errors(c["expected"])):`
- 왜 위반: HTTP.md I2는 "클라이언트가 파일마다 content_hash를 대조", 게시 때 서버가 EVAL의 `scenario_id` 비중첩을 검증("경계 규칙 9")한다고 정한다. 모델랩은 매니페스트 해시·파일 해시·스키마·누수를 한 번 더 한다(`check_no_leak`의 docstring이 "I2도 확인하지만 다시 본다"로 스스로 밝힌다). I7 `review` 호출도 `expected`/`reviewed_input`을 모델 출력 스키마로 서버가 검증(HTTP.md I7 표)하는데 `regression.py`가 다시 검증해 `input_invalid`/`expected_invalid`로 건너뛴다. 브리프 4절("학습 데이터에 평가셋이 섞이지 않는다")이 근거라면 그것을 보증하는 것은 I2이므로 재검사 대신 I2 보증을 믿거나 계약 시험으로 올린다. 단 `UNSUPPORTED_VERSION`(문진 스키마 정수 버전, `datasets.py:44-45`)은 영역 바깥 입력과 자기 지원 버전의 대조라 해당하지 않는다.
- 최소 수정: 클라이언트·I2·I7이 보증하는 검증은 제거하고, 누수 검사는 I2 계약 시험(헤드)에 둔다. 필요하면 "다시 본다"를 방어 의도로 ADR에 남기되 R7과 충돌하므로 헤드 판단 필요.

**ML-06 | R3·R4·C | P2 | 평가->지표 사이가 비공개 함수와 문자열 키 기록에 의존**
- 위치: `model_lab/metrics.py:21`, `:58-117`; `model_lab/regression.py:15`; `model_lab/evaluate.py:158-241`
- 인용: `from .evaluate import _sum_c, a_rates, sample_dims, sample_qs` / `lambda r: r["rules"]["ends_ok"]`
- 왜 위반: `metrics.py`가 `evaluate.py`의 `_sum_c`(밑줄 비공개)와 기록 dict의 키(`c`, `rules`, `prot_exact`, `raw_valid`, `violations`, `outcome`, `material_ok`, `response_len`, `is_fallback`)를 직접 알고, `regression.py`도 `aggregate`/`evaluate_sample`을 직접 호출한다. 기록 형식이 어디에도 타입으로 정의돼 있지 않다.
- 최소 수정: 평가 기록을 `TypedDict`/데이터클래스(`EvalRecord`)로 한 곳에 정의하고 지표는 그 타입만 받는다. `_sum_c`를 공개 함수로 올리거나 지표 쪽으로 옮긴다.

**ML-07 | R3·C | P2 | train_wrap과 attach가 서로의 비공개 함수·파일 형식에 기댄다(순환)**
- 위치: `model_lab/attach.py:101`, `:183`; `model_lab/train_wrap.py:26-27`; 기록 읽기 `attach.py:86-110`
- 인용: `from .train_wrap import _sha256` / `from .attach import PROFILES_DIR, check_adapter` / `from .config import ROLES, _read, check_inline_secrets` / `from .pipeline import make_registry`
- 왜 위반: attach <-> train_wrap이 서로를 import(함수 안에서 지연 import로 순환 회피), 둘 다 `config._read`(비공개)를 쓴다. `*.train-wrap.json`(`record_version: 1`)은 train_wrap이 쓰고 attach가 `rec.get("adapter")["files"]`, `rec.get("hyperparams")` 같은 키로 읽는데 스키마가 코드 안 if 검사뿐이다(`train_wrap.py`의 `_SCHEMA`는 설정용이고 기록용이 아니다). attach는 pipeline의 `make_registry`까지 부른다.
- 최소 수정: 학습 기록용 JSON Schema/데이터클래스를 한 곳에 두고, 공용 함수(`sha256`, 프로파일 로더, 레지스트리 팩토리)를 공용 모듈로 옮긴다.

**ML-08 | R1·E | P2 | 학습 서버의 데이터 형식·경로·스크립트 이름을 설정과 변환 코드가 안다(계약에 없는 경계 형식)**
- 위치: `configs/train.yaml:36`, `configs/train-b.yaml:36`, `model_lab/transform.py:57-105`(서버 messages 변환기), `model_lab/train_wrap.py:43-60`(arg_map, 스크립트 이름 패턴)
- 인용: `dir: "/home/openit/taehyeon/data/dataset_10_14"` / `# 학습 서버(~/taehyeon/data/dataset_10_14)의 한 줄은 {"scenario_id", "split", "human_review", "messages"}`
- 왜 위반: 학습 서버는 `areas.json`의 영역이 아니라 영역 밖 시스템이므로 R1의 "이웃 영역"은 아니지만, 영역 경계를 넘는 형식(서버 JSONL `messages`, `<prefix>_{train,val,test}.jsonl`, 서버 스크립트 CLI 인자, `run_config.json`)이 boundary-contracts에 없다(E). 이 경로로 들어오는 데이터는 I2를 거치지 않아 `dataset.content_hash`가 자기 신고값이다(`train.yaml:39-40` 주석 "서버 run 기록에 없다(미확인)"). 절대 경로와 담당자 이름(`taehyeon`)이 커밋된 설정에 들어 있다. ADR 0004는 사용자 결정(D78)이라 구조 자체는 정당하나 형식의 계약화는 없다.
- 최소 수정: 서버 경로는 환경 변수/비밀 아닌 설정 입력으로 빼고(커밋하지 않는다), 서버 데이터셋->I2 데이터셋 변환 책임과 서버 산출물 형식을 헤드에 변경 요청으로 올려 계약화 여부를 정한다.

**ML-09 | R1 | P2 | 문진 스키마 폴더 배치(`CONTRACT_VERSION`이 `schema_dir` 상위)와 사본 요구**
- 위치: `model_lab/schemas.py:13-18`, `model_lab/config.py:222-226`, `tests/conftest.py:11-17`, `configs/gpu-dev*.yaml`의 `schema_dir: ../vendor/survey-schema/schemas`, `docs/runbook.md:75`
- 인용: `p = Path(schema_dir).parent / "CONTRACT_VERSION"` / `return Path(survey_schema.contract_version_dir(1)) / "schemas"`
- 왜 위반: 이웃(survey-service)이 배포한 패키지의 내부 폴더 배치(`schemas/` 상위의 `CONTRACT_VERSION`)를 경로 연산으로 안다. `contract_version_dir(1)`의 `1`이 두 곳에 상수로 박혀 있어 정수 버전이 바뀌면 코드 수정이 필요하다. GPU 호스트용 설정은 `vendor/survey-schema/` 사본을 쓰게 하고(gitignore) runbook이 사본 만들기를 안내한다. 공통 규칙은 설치 패키지로 읽고 복사본을 만들지 않는 것이다. (`../../survey-service/schema/schemas` 경로는 `gpu-dev.yaml:9` 주석에만 있고 읽기 허용 참조의 범위다: P3.)
- 최소 수정: `survey_schema`가 공개 API로 `CONTRACT_VERSION`과 스키마 경로를 주게 하고(survey-service 변경 요청), 호스트에서도 태그 설치로 통일.

**ML-10 | R5·E | P2 | 회귀 평가가 비식별 TurnTrace의 내부 키를 읽고 INTERFACES 지침과 어긋남**
- 위치: `model_lab/regression.py:23-24,66-69`
- 인용: `TRACE_KEYS = {"LORA_A": {"input": "extract_input", "output": "extract_output"}, ...` / `inp = trace.get(TRACE_KEYS[role]["input"])`
- 왜 위반: INTERFACES.md:136은 `get_trace` 결과를 "TurnTrace로 검증하지 말고 구조 정보(열거값, 식별자, 개수, 순서)만 쓰라"고 한다. 모델랩은 trace 안의 모델 입력 객체(발화를 포함할 수 있는 `extract_input`)를 키 이름으로 꺼내 학습 평가 입력으로 쓴다. README가 이미 열린 항목 D-E("키 이름이 추정")로 적고 있다. 키 이름은 문진 스키마(허용된 참조) 소관이지만 입력 소스로서의 사용은 계약에 근거가 없다. `reviewed_input` 경로(D55)는 계약에 있다.
- 최소 수정: 트레이스 읽기는 제거하고 `reviewed_input`이 있는 사례만 쓰거나, 헤드에 "회귀 입력은 FailureCase에 포함" 변경 요청.

**ML-11 | R3 | P2 | 레지스트리 서버가 레지스트리 구현의 비공개 정적 메서드를 호출**
- 위치: `model_lab/service/app.py:279`
- 인용: `items, nxt = FileModelRegistry._page(vs, _one(q, "cursor"), _limit(q))`
- 왜 위반: 서비스(컴포넌트 10)가 `FileModelRegistry._page`(밑줄)를 직접 쓴다. 또 `list_versions` HTTP 응답에서 페이지네이션 책임이 서버/레지스트리로 갈라진다.
- 최소 수정: `_page`를 공개 이름으로 올리거나 `list_versions`에 `cursor/limit`를 지원하는 공개 메서드를 둔다.

**ML-12 | F(브리프)·E | P2 | 어댑터 얹기 경로가 `lineage.question_sets`를 비운다**
- 위치: `model_lab/attach.py:168`, `model_lab/packager.py:73`(`training_question_sets`)
- 인용: `dataset_manifest={"files": []}`
- 왜 위반: brief 3-1은 "`lineage.question_sets`에 학습에 쓴 문항 세트를 채운다(게이트가 참고, D42)"이다. `package-adapters`는 데이터셋 매니페스트 없이 호출해 항상 빈 목록을 게시한다(경고도 없음; 학습 기록 경고만 있음). `evaluated` 지표(`attach.py:72`)도 레지스트리에 없는 이름이다(ML-01과 같은 부류).
- 최소 수정: 데이터셋 ref로 I2 매니페스트를 읽어 문항 세트를 채우거나, 비어 있음을 `warnings`와 `ext`에 명시하고 게이트와 합의(변경 요청).

### P3

**ML-13 | F | P3 | `/readyz`가 총 2초 상한과 사유 본문이 없다**
- 위치: `model_lab/service/app.py:155-165,250-253`
- 인용: `return self._json(200 if ok else 503, {"status": "ready" if ok else "not-ready"})`
- 왜: CONVENTIONS.md:111는 의존이 응답하지 않을 때도 총 2초 안에 503과 사유를 답하라고 한다. 이 서버의 의존은 로컬 디스크뿐이고 `ready()`가 `list_ids`와 쓰기 프로브를 시간 제한 없이 실행하며 사유 문자열이 없다. 시험(`tests/test_service.py:113-117`)은 루트가 못 쓸 때만 본다. 실제로 느린 디스크에서의 시간은 측정하지 않았다.
- 최소 수정: 프로브에 마감(2초)을 걸고 본문에 `reason`(접속 정보 제외)을 넣는다.

**ML-14 | D89 | P3 | HTTP 클라이언트 timeout 300초의 근거가 ADR에 없다**
- 위치: `configs/gpu-dev.yaml:11,27`(`timeout: 300`)
- 인용: `dataset_store: {kind: http, base_url_env: DATASET_STORE_URL, token_env: DATASET_STORE_TOKEN, timeout: 300}`
- 왜: 공통 규칙은 timeout 값의 근거(측정·표준·제약)를 ADR에 요구한다. `docs/adr`와 runbook에서 300의 근거를 찾지 못했다(grep). CONVENTIONS.md의 timeout 표와 대조도 없다.
- 최소 수정: 근거를 ADR 0007에 추가하거나 "측정 안 함"으로 명시.

**ML-15 | R1 | P3 | 설정·프로파일 주석의 이웃 폴더 경로와 수동 동기화 값**
- 위치: `configs/gpu-dev.yaml:9`, `profiles/qwen3-8b.yaml:3-4`, `profiles/gpu-dev-12g.yaml:25`
- 인용: `model-runtime의 서빙 프로파일 ../model-runtime/profiles/rtx4090-24g.json과 base_model·max_lora_rank(16)를 맞췄다`
- 왜: 이웃 폴더 경로와 이웃 설정 값(`max_lora_rank`)을 손으로 맞춘 것을 주석으로 적었다(코드 연결 없음은 파일이 스스로 밝힘). 이 영역은 해당 폴더를 읽는 것이 허용되지 않는다. 값 불일치는 게시 전 검사(`checks.max_lora_rank`) 때문에 숨은 결합이다.
- 최소 수정: 이웃 경로를 지우고 model-runtime이 배포한 값(`serving_config.max_lora_rank` 등 I9 `info`)을 읽는 방법을 변경 요청.

**ML-16 | R5 | P3 | 모델랩이 시스템 프롬프트를 학습 데이터와 별도 파일로 이중 관리**
- 위치: `prompts/lora_a.system.txt`, `README.md` 표("서버 데이터와 같은 시스템 프롬프트")
- 왜: 학습 서버 데이터의 system 글자 사본이다. 서버 파일과 같은지는 이 점검에서 대조하지 못했다. `transform.read_server_file`이 한 종류여야 중단하는 검사가 있다.
- 최소 수정: 학습 서버가 정본이면 갱신 절차를 runbook에 명시.

**ML-17 | R3 | P3 | 역할 분기가 여러 모듈에 흩어져 있음**
- 위치: `evaluate.py:135,161,224`, `metrics.py`의 `if role == "LORA_A"`, `regression.py:20-35`
- 왜: A와 B의 평가가 `role` 분기로 한 함수 안에 같이 있다. 역할 수가 고정(2)이고 입출력 형식이 달라 분리 이득이 크지는 않지만 R3 관점에서는 A용/B용 평가기를 따로 두면 `evaluate_sample` 하나가 두 컴포넌트 일을 하는 모양이 사라진다.
- 최소 수정: 역할별 평가기를 `Protocol` 구현으로 나눈다.

## 3. B. R3·R4 요약

- 다른 컴포넌트의 비공개 이름을 쓰는 곳: ML-06(`_sum_c`), ML-07(`_sha256`, `_read`), ML-11(`_page`). 설정 필드를 이웃 컴포넌트가 직접 읽는 곳: `pipeline.py`가 `cfg["serving"]["LORA_A"]["constraint"]["supported_features"]`를 백엔드 선택에 사용(`pipeline.py:291`), `backends`는 LORA_A 설정만 받는다(`pipeline.py:282` `role_training(self.cfg, "LORA_A")`로 B의 학습 설정은 쓰지 않는다. A와 B가 같은 기반 모델을 쓴다는 가정으로 보이나 근거 주석은 없음, 확인하지 못함).
- 전역 상태: `schemas.py`의 `lru_cache`된 검증기(읽기 전용, 문제 없음). 서비스 상태는 `Service` 인스턴스에 있음. 전역 가변 상태는 찾지 못했다(`model_lab` 안 `global` 검색 0건).

## 4. C. R5 요약

기계가 읽는 정의가 있는 곳: 설정(`config.py CONFIG_SCHEMA`), 학습 설정(`train_wrap.py _SCHEMA`), `TrainRequest`(dataclass), 계약 스키마(`boundary_contracts.validator`). 없거나 암묵적인 곳: 평가 기록 dict(ML-06), 단계 결과·`run.json`의 `stages` 구조(ML-03), `*.train-wrap.json`(ML-07), 변환 결과 JSONL `{sample_id,prompt,completion}`(주석만, `trainers/base.py:15`).

## 5. D. R1 요약

이웃 패키지 import: `boundary_contracts`(허용), `model_runtime`(허용 라이브러리), `turn_engine`(허용 라이브러리이나 ML-04), `survey_schema`(허용). 다른 영역 코드 import는 없음(`from .*synthetic|ops_feedback|release_gate` 검색 0건). 이웃 구조를 아는 곳: ML-02, ML-09, ML-10, ML-15. 이웃 환경 변수 이름: `DATASET_STORE_URL/TOKEN`, `FAILURE_STORE_URL/TOKEN`, `MODEL_REGISTRY_*`는 이 영역 설정의 입력 이름이고 이웃 이름이 아니다(`docs/boundary.md`가 `deploy/compose.yaml`을 배선 정본으로 지목). 이웃 포트·호스트: 코드에 없음(`configs/gpu-dev*.yaml`은 환경 변수로 받음).

## 6. E. R6: 영역 간 인터페이스와 계약 소재

| 인터페이스 | 방향 | 계약 스키마 | 비고 |
| --- | --- | --- | --- |
| I3 ModelRegistry | 제공 | `ModelPackageManifest`, `EvaluationReport`(HTTP.md I3) | 있음 |
| I2 DatasetStore | 소비 | `DatasetManifest`, `ActionExtractionSample`, `ResponseSample`(HTTP.md I2) | 있음 |
| I7 FailureStore | 소비(회귀, 기본 꺼짐) | `FailureCase`, 비식별 trace | trace 내부 키는 계약 밖(ML-10) |
| I1 QuestionReader, I4 | 미사용 | | 설정 자리만(`config.py:105`) |
| I9 | 라이브러리(`model_runtime`)로 사용, HTTP 아님 | `serving_config`(model-runtime 소유) | 설정 `serving:` 형식은 model-runtime이 정함 |
| I5 GateService | **소비하지 않음** | | `submit_candidate` 등을 코드·문서·설정에서 찾지 못했다(검색 0건). 모델랩 `model-lab` 역할은 I5 접수가 허용돼 있으나 현재 접수는 다른 주체가 한다고 문서(`README.md`)가 적는다. 실제 접수 주체는 확인하지 못했다. |

계약에 없는 경계 형식(모두 나열):
1. 학습 서버 데이터셋 JSONL(`messages`, `<prefix>_{train,val,test}.jsonl`) 및 서버 스크립트 CLI 인자·산출물 위치(`configs/train*.yaml`).
2. `*.train-wrap.json` 학습 기록(`record_version: 1`)이 `package-adapters` 입력이며 패키지 `ext."model-lab".lineage`로 흘러간다(비계약 필드; boundary.md가 "비계약 필드"로 명시).
3. 패키지 `ext."model-lab"`(run_id, config_hash, seed, train_data_hash, detail 등)와 `EvaluationReport.ext."model-lab".detail`: 게이트는 해석하지 않지만 형식은 `ext`라 자유(계약이 허용).
4. 평가 입력: 샘플의 `question_set` 필드를 파이프라인이 채움(서버 데이터에는 없음, `pipeline.py:212`): `ActionExtractionSample`에 있는 필드이나 서버 데이터 경로에서는 모델랩이 추정해 넣는다.
5. 모델 패키지의 `system_prompt` 파일 배치는 계약(INTERFACES I3·D75)에 있음. 문제 없음.
6. 영역 안에서 정의되었으나 경계를 넘는 것: `EvaluationReport.ext."model-lab".detail`의 `report_version: 2` 구조(design.md 6절)는 사람이 읽는 첨부이며 게이트가 읽지 않는다고 INTERFACES I3가 명시: 문제 없음.
7. 설정 파일(`config_version: 1`)과 `run.json`은 영역 내부 형식이며 deploy 스케줄러가 `run-once` 종료 코드(0/2/3)만 쓴다. 이 종료 코드 규약은 `docs/design.md` 9절에만 있고 boundary-contracts·deploy 문서와 대조하지 못했다(읽지 못함).

## 7. F. 계약 준수 한 줄씩 (I3 서버 / I2 클라이언트 / I5)

대조 근거: `HTTP.md` I2·I3, `CONVENTIONS.md` 9절, `INTERFACES.md` I3. 시험: `tests/test_service.py::test_i3_server_conformance_with_real_client` 통과(196 passed 안). 계약 패키지의 `conformance` 모듈에는 I3 참조 서버/적합성 시험이 없고(`ReferenceServer`, `RuntimeReferenceServer`, `StaffCall*`, `PublicApi*`만 있음) I3는 모델랩 자체 시험(실제 `HttpModelRegistry` 클라이언트)이 유일한 적합성 근거다.

| 항목 | 계약 | 구현 | 판정 |
| --- | --- | --- | --- |
| `list_ids` | `GET /v1/packages` 목록 | `app.py:272-274`, `{"items","next_cursor"}`, limit 기본 100 최대 500(`_limit` `app.py:333-340`) | 일치 |
| `list_versions` | `GET /v1/packages/{id}/versions`, 오래된 것부터 | `app.py:275-280` | 일치(ML-11 비공개 호출) |
| `get_manifest` | `GET .../versions/{version}` | `app.py:283-284` | 일치 |
| `open_adapter` | tar, 클라이언트가 `directory_hash` 대조 | `app.py:285-290`, `application/x-tar`, 서버도 해시 검증 | 일치(서버 검증은 R7 관점의 초과이나 계약이 서버 쪽 해시를 요구: "서버는 `content_hash`를 계산해 담는다") |
| `get_eval_report` | `application/octet-stream` 바이트 | `app.py:292-294` | 일치 |
| `publish` | 쓰기 역할 `model-lab`, 201/200/409, 응답은 매니페스트 | `app.py:340-390`(`existed` 선검사 후 201/200), 409는 `registry.publish`의 `CONFLICT` | 일치 |
| 읽기 토큰의 쓰기 | 403 | `app.py:144-151` | 일치 |
| 토큰 없음/틀림 | 영역 사이 API는 403(CONVENTIONS:86) | `PERMISSION_DENIED`->403 | 일치 |
| 과다 요청 | 503 `UNAVAILABLE`(CONVENTIONS:87) | 연결 상한 `app.py:439-449`, 동시 게시, 인증 실패 제한 모두 503 | 일치 |
| 상태 코드 표 | CONVENTIONS 78 | `app.py:38-39` STATUS 표 | 표를 CONVENTIONS 표와 한 줄씩 대조하지는 않음(`INVALID_RECORD` 400, `CONFLICT` 409, `UNAVAILABLE` 503, `INTEGRITY_ERROR`·`UNSUPPORTED_VERSION` 422, `FAILED_PRECONDITION` 412는 HTTP.md I9와 같다는 것만 확인) |
| 토큰 파일 입력 | `<이름>_TOKENS_FILE`(D84) | `read_secret("MODEL_REGISTRY_READ_TOKENS")` `app.py:455-456` | 일치 |
| `/healthz` | 의존과 무관 200 | `app.py:250` | 일치 |
| `/readyz` | 총 2초, 503+사유 | 시간 제한·사유 없음 | **어긋남 ML-13** |
| tar 안전 | `..`/절대경로/심볼릭 링크 거부 | `service/tarutil.py`(읽음: 49줄, 거부 규칙 세부는 시험 `test_hardening.py`로만 확인, 줄별 대조는 안 함) | 부분 확인 |
| I2 클라이언트 | `HttpDatasetStore`, 생성자 `base_url,token,timeout` | `factories.py:36-48` `build_http`, 사용 호출 `list_versions`,`get_manifest`,`open_file`(`datasets.py:50`) | 일치. 실제 서버(synthetic-data)와 맞춘 시험은 하지 못했다(README "실제 클라이언트로는 아직 시험하지 못함") |
| I2 `download`(묶음) | 권장 경로 | 파일마다 `open_file`로 받음 | 계약상 허용 |
| I5 | 소비 없음 | 해당 코드 없음 | 점검 대상 없음(위 6절) |
| 라벨 | `io.openit.*` 6개(D50) | `Dockerfile:35-40`, 비소비 값은 `"n/a"` | 일치 |
| 비root, healthz | | `USER 10001`, HEALTHCHECK | 일치 |

## 8. G. R7 이중 검증·재구현

- ML-05(I2 해시·누수·I7 expected/입력 재검증).
- `check_adapter`/`stage_package`의 `max_lora_rank` 확인(`pipeline.py:354-359`, `attach.py:57-58`): model-runtime이 엔진 적재 전에 같은 검사를 한다(INTERFACES.md:204). 게시 전에 먼저 거르는 의도(조기 실패)라고 보이나 근거 문서를 찾지 못했다. 이중 검증이다(P3 수준, 별도 번호 안 붙임).
- `registry.publish`가 `ModelPackageManifest` 스키마·`content_hash`·`adapter_hash`·`serving_config_hash`·시스템 프롬프트 해시를 검증하고(`design.md` 8절), `packager.build_manifest`도 같은 스키마를 `validator(...).validate`와 `verify_system_prompt`로 검증한다(`packager.py:54,78`). 한 영역 안 컴포넌트 둘이 같은 규칙을 다시 한다(R7). P3.
- 평가의 학습 서버 채점 규칙 재구현: `evaluate.score_a`, `text_rules.py`는 학습 서버 `score_a_v2.py`/`eval_lora_b_v2.py`를 자기 방식으로 다시 쓴 것이며(`evaluate.py:1-9`) 서버 쪽과 같은 값인지 대조 시험을 하지 않았다(동일 데이터 비교는 이 점검에서 못 함).
- 시맨틱 위반 판정: ML-04(재구현이 아니라 turn-engine 호출. 재구현은 없음).

## 9. 문제 없음으로 확인한 것

- 이웃 영역 코드 import 없음: `model_lab`의 import 전체를 위 grep으로 나열해 `boundary_contracts`, `model_runtime`, `turn_engine`, `survey_schema`만 확인.
- 비밀: 서비스 토큰과 클라이언트 `*_env`는 모두 `read_secret`(`factories.py:34`, `app.py:455`). 설정에 직접 비밀을 쓰면 `check_inline_secrets`가 거부(이름만 확인, 본문은 읽지 않음).
- 서버는 `/healthz`, `/readyz`, 표준 출력 로그, 비root, 라벨 6개(`Dockerfile:32-40`).
- 지표 임계치·합격 판정 키가 설정 스키마에 없음(`design.md` 3절 및 시험 `test_config_rejects_unknown_keys_and_thresholds`; 시험 이름만 확인).
- 의존 핀: `requirements.txt`가 boundary-contracts v2.1.1, turn-engine v0.4.2, model-runtime v0.7.6(`deploy/scripts/lib.sh`와의 일치는 읽지 못해 대조 안 함).

## 10. 점검하지 못한 것

- `deploy/scripts/lib.sh`(버전 기준표), `deploy/compose.yaml`, 이웃 영역 문서(읽기 허용 범위 밖).
- `model_lab/trainers/hf.py`, `backends/hf.py`, `registry.py` 전체(저장 구현 세부), `service/tarutil.py` 세부, `text_rules.py` 전체, `config.py` 1-100줄 일부, `train_wrap.py` 80-349줄 세부(명령 조립·doctor).
- 모든 `tests/`의 본문(import와 일부 줄만 확인), `scripts/*`의 본문(grep만).
- 이미지 빌드·스모크(`scripts/smoke_registry.sh`)는 Docker 실행을 하지 않았다. 새 환경·두 번째 환경 검증도 하지 않았다(README 5절의 한 PC 결과만 재현: 196 통과).
- 실제 I2·I7 서버와의 통신(서버가 없음), GPU 호스트 접속(금지 범위), 학습 서버 스크립트 내용.
- `fixtures/`, `vendor/`, `runs/`(gitignore)의 내용.
- 줄별 STATUS 표 대조(`app.py` STATUS vs CONVENTIONS 78)는 일부만 확인.
