# STATUS (2026-10-27)

## 14차 갱신: 24GB급 프로파일 qwen3-8b, 브랜치 main
- 한 것: `profiles/qwen3-8b.yaml`, 런북 프로파일 표, 시험 2건 추가(pytest 152건 통과). 이제 `package-adapters --profile qwen3-8b`를 쓸 수 있다.
- 14B 프로파일은 만들지 않았다(A만 학습됨, 24GB bf16 불가).
- 미확인: 실제 8B 어댑터의 `base_model_name_or_path` 값(가짜 어댑터로만 시험), 8B 서빙 적재(GPU 구멍), model-runtime 프로파일의 `status`는 `unverified` 그대로.

## 13차 갱신: 문서 낡음 정정 (코드 변경 없음), 브랜치 main
- 고친 것(근거는 현재 파일 확인): `docs/runbook.md` 시험 개수 80건 -> 150건(`pytest --collect-only -q`가 150건 수집, 통과 실행은 안 함), GPU 절 wheel 예시 -> turn-engine v0.4.0, model-runtime v0.7.0(`requirements.txt`). `docs/boundary.md` 머리말·EvaluationReport·소비 표를 v2.0.2 / turn-engine v0.4.0 / model-runtime v0.7.0으로, I7을 "쓰지 않음"에서 구현 사실(`model_lab/regression.py`, `configs/gpu-dev.yaml`, runbook 회귀 절)로. `docs/design.md` 10절의 I7 "쓰지 않는다"도 같이 정정.
- 알려진 낡은 것(코드·설정 영역이라 건드리지 않음): (1) `pyproject.toml`과 `model_lab/__init__.py`의 버전은 0.1.0인데 `CHANGELOG.md` 최신은 0.2.3(제안, 태그 없음)이다. (2) `configs/gpu-dev-fixture-*.yaml` 주석의 "golden으로 만든 가짜 데이터/golden에서 시험용 데이터셋 저장소를 만든다"는 낡았다: `tests/golden/`은 0.2.3에서 제거됐고 데이터는 `scripts/make_fixture_dataset.py`가 합성 v2 샘플로 만든다(runbook 기준). 데이터셋 id `fx-golden`은 이름만 남은 것이다. 이 주석 수정은 코드 영역 변경으로 별도 단위.
- 미확인: `docs/boundary.md`·`docs/design.md`의 `HttpModelRegistry(v1.2.0)` 표기가 현재 계약 태그와 맞는지 확인하지 않았다. 아래 11차의 `pip install -r requirements.txt` 전체 해결도 여전히 미시도.

## 12차 갱신: 학습 서버 정본 감싸기 (D78, G06), 브랜치 main
- 한 것: `configs/train.yaml` + `python -m model_lab train [--dry-run]` + `train-doctor`(`model_lab/train_wrap.py`), 프로파일 `qwen3-4b`, 런북 절, `trainers/hf.py` 비정본 경고(삭제 안 함: 사용처 있음), 시험 `tests/test_train_wrap.py`(서버 스크립트는 대역). CHANGELOG 0.2.3에 결정과 이유.
- 못 한 것 [구멍]: 이번 세션에 학습 서버 ssh가 연결 거부(4회)라 서버 스크립트의 인자 이름·산출물 위치·run 기록 키를 확인하지 못했다. `server.arg_map`/`args_verified: false`로 표시했다. 진짜 학습은 GPU 구멍. 파이프라인 train 단계의 감싸기 연결(데이터 형식 messages vs prompt/completion)은 인자 확인 뒤.

## 11차 갱신: 비밀 파일화, 어댑터 얹기 (D84), 브랜치 `train`
- boundary-contracts v2.0.2로 올림(requirements*, lock, Dockerfile `BC_SPEC`·라벨). 서비스와 `token_env`가 `read_secret`(NAME_FILE 우선)으로 비밀을 읽는다. 시험 `tests/test_secrets.py`.
- 어댑터 얹기: 이전에는 어댑터를 받아 패키징만 하는 길이 없었다(패키징은 파이프라인 안의 단계뿐). `model-lab package-adapters`를 추가했다. 가짜 어댑터로 시험(`tests/test_attach.py`). 진짜 어댑터 서빙은 GPU가 필요해 확인하지 못했다.
- 알려진 것: `.venv`의 turn-engine은 0.3.0이고 requirements의 v0.3.1 메타데이터는 boundary-contracts v2.0.1을 고정한다고 CHANGELOG에 적혀 있다. v2.0.2와 pip 해결이 안 될 수 있어 헤드가 turn-engine 태그를 맞춘다(이번에 올리지 않음) [미확인: `pip install -r requirements.txt` 전체 해결은 시도하지 않았다].

## 10차 갱신: 파이프라인을 model-runtime v0.6.0 / turn-engine v0.3.0으로 이전 (D74, D75)
브랜치 `migrate-contracts-2.0.0`(push·태그 안 함).

**한 것 [확인: `pytest` 113건 통과]**
- `.venv`와 `requirements.txt/lock`: model-runtime `v0.6.0`, turn-engine `v0.3.0`(boundary-contracts `v2.0.0`). 설정의 서빙 값은 prompt-3 / protect-2.
- 변환: `transform.to_example`이 `render_prompt(system_prompt=…)`로 prompt를, 서버 assistant 글자와 같은 completion을 만든다. 학습 직후 시스템 프롬프트를 어댑터 디렉터리에 넣는다.
- 평가: A는 `extract_raw`(보호 없음), B는 `respond_raw`·`respond`. A 지표 이름·규칙은 `score_a_v2.py`, B는 끝맺음·다음 질문·새 숫자·금지 표현·문장 수(`text_rules.py`). EvaluationReport 스키마는 계약 그대로. 상세 보고서 `report_version: 2`.
- 회귀: v2 `FailureCase`(LORA_A/LORA_B, 객체/문자열 expected)와 입력 검증.
- 시험: 합성 v2 fixture, 30건 실패(+ v0.6.0 설치 뒤 추가로 깨진 golden·transform·비교 스크립트 5건) 모두 해소. 이전 커밋에서 `pipeline.py`에 중복으로 들어간 코드 조각(두 번째 `stage_train` 등)을 제거했다.
- 간헐 실패하던 `test_clean_model_cache_dry_run_then_delete_via_hub_api`를 고쳤다(시험 재료가 두 모델에 같은 커밋 해시를 써서 어느 모델이 지워질지 정해지지 않았다).
- 두 번째 환경: 새 venv에 `requirements.txt`로 설치해 `pytest` 112 통과·1 건너뜀(huggingface_hub 없음). 같은 PC이고 이웃 `../survey-service/schema`를 읽으므로 독립된 머신 확인은 아니다 [미확인: GPU 호스트, Docker 이미지 스모크는 이번에 돌리지 않음(서비스 이미지는 바뀌지 않음)].

**하지 않은 것·미정 [CR-L008]**
- 학습기(`trainers/hf.py`) 손대지 않음(정본 결정 대기). 서빙(JCS)과 서버 학습 데이터의 user 글자 불일치, 위험 신호의 v2 표현, 회귀 trace 입력 키(추정), METRICS.md 갱신, `validate_extraction` 지표화(제안).
- 실제 모델·GPU 평가는 하지 않았다(오라클 모델 시험). 지표 값은 의미가 없다.
- `tests/golden/`(v1 정적 예시)는 쓰는 곳이 없어 제거했다(12차 이후, G09).

## 9차 갱신: boundary-contracts 2.0.0 이전 (D74, D75) — (10차에서 파이프라인 이전 완료)
지시서: `head/docs/migration-2.0.0.md`. 브랜치 `migrate-contracts-2.0.0`(push·태그 안 함).

**한 것 [확인]**
- 의존을 boundary-contracts `v2.0.0` 태그로(requirements*.txt/lock, Dockerfile의 `BC_SPEC`·라벨). 이미지 스모크 통과(`scripts/smoke_registry.sh`: healthz/readyz, 비root, 라벨 v2.0.0, 게시·조회). 스모크는 `requirements-service.txt`의 태그에 맞는 wheel을 고르도록 고쳤다.
- 학습 서버 코드와 이 리포의 관계(아래)를 읽어 확인했다.
- 변환기 `transform.py` 2.0.0 절: 서버 `messages` <-> `ActionExtractionSample`/`ResponseSample`. 서버 줄 10,264개 전부 검증 통과·글자 단위 왕복 일치(`scripts/check_server_dataset.py`, 서버 데이터는 읽기 전용으로 받아 `runs/_server_ds/`에 복사해 시험, 커밋 안 함).
- 패키징: `packager.install_system_prompt` + `build_manifest(system_prompt=...)`로 `ModelPackageManifest.system_prompt` 채움. 프롬프트는 어댑터 디렉터리 안에 들어가 `adapter_hash`에 포함. 레지스트리(I3 구현)가 파일 존재·해시를 확인(`INTEGRITY_ERROR`). 설정에 `roles.<역할>.system_prompt_file`(필수), 리포 `prompts/`에 서버 데이터의 프롬프트를 그대로 둠.
- `datasets.py`/`config.ROLE_TO_FILE_ROLE`: 파일 역할 `LORA_A`/`LORA_B`, 샘플 검증은 `ActionExtractionSample`/`ResponseSample`(문진 스키마 참조 없음). `metrics.py`의 보고서 버전 문자열을 계약 상수로.

**학습 서버 코드와의 관계 [확인: `~/taehyeon/train_lora_a.py`, `~/myungchan/train_lora_b.py` 읽기만]**
- A·B 학습은 서버의 별도 코드로 이미 이뤄진다. 두 파일은 기본값 5개(데이터 경로, prefix, 모델, 출력 폴더, eval_steps)만 다르고 나머지는 같다(diff 확인). TRL SFTTrainer + PEFT, 기본 `Qwen/Qwen3-4B`, LoRA r=16, alpha=16, dropout 0, 모든 선형층(q,k,v,o,gate,up,down), bf16 전정밀(양자화 없음), lr 2e-4, epochs 3, batch 2 x accum 4, max_len 4096, seed 42, eval_steps마다 검증 손실·체크포인트, 최저 검증 손실 체크포인트로 어댑터 저장.
- 입력 형식: `{prefix}_{train,val,test}.jsonl`의 `messages`(system, user, assistant)를 채팅 템플릿(`enable_thinking=False`)으로 만들고 **assistant 토큰에만** 손실. 시스템 프롬프트는 데이터 줄 안에 있다.
- 이 리포의 학습기(`trainers/hf.py`)와는 다르다: 이쪽은 `prompt`/`completion` 문자열 쌍을 따로 토큰화하고(채팅 템플릿 없음), 4비트 양자화·gpu-dev 프로파일(Qwen2.5-1.5B, q/k/v/o만)을 쓴다. 따라서 **지금 이 리포의 학습기로는 서버 학습과 같은 어댑터를 만들 수 없다.** 어느 쪽을 정본으로 할지(서버 코드를 설정으로 감싸 이 리포가 호출 / 이 리포 학습기를 채팅 템플릿으로 고침 / 서버에서 학습하고 이 리포는 평가·패키징·게시만)는 [미정, 헤드·사용자 결정]. 나는 구현하지 않았다.
- 서버의 `lora_a_*`와 `lora_b_*`는 시나리오가 같고(5,077개 공유) 분할 사이에 걸치지 않는다 [확인].

**못 한 것·막힌 것 [확인]**
- 파이프라인 단계(변환 -> 학습 -> 평가)와 그 시험은 v1 그대로다. 막힌 이유: 평가와 학습 데이터 변환이 model-runtime(`render_prompt`, `*_raw`, 보호)에 걸려 있는데 v2용 새 태그(v0.6.0 제안)가 아직 없다(원격 태그는 v0.5.3까지 확인했다. 그 안이 v2 기준인지는 다른 리포를 읽지 못해 확인하지 못했다). turn-engine도 v0.2.0뿐이다. 시험 fixture(`tests/doubles/fixtures.py`)도 v1 golden 기반이라 DatasetManifest 위반으로 파이프라인 시험이 모두 선 채로 멈춘다.
- 평가·회귀의 v1 용어(`risk_signal_*`, `intent_accuracy` 등) 정리: 하지 않았다(평가기가 v1 출력 형식에 묶여 있어 model-runtime 태그와 함께).
- `trainers/hf.py`의 채팅 템플릿 학습, `to_chat_example`을 파이프라인에 연결하는 일: 위 [미정]이 풀린 뒤.

**환경** `.venv`의 turn-engine·model-runtime이 로컬 경로 editable 설치로 남아 있어(다른 세션이 고치는 작업 트리) 태그 v0.2.0·v0.4.0 설치로 바꿨다. boundary-contracts는 `@v2.0.0` 태그 설치.

## 8차 갱신: 용량 보호를 사용량 예산으로 교체
- WSL `df`는 호스트 여유를 알려 주지 않으므로(가상 디스크 크기, vhdx는 지워도 줄지 않음) `min_free_gb`/`df` 점검을 없애고 `max_footprint_gb`(HF 캐시 + ~/openit-gpu + 후보 예상 크기) 예산으로 바꿨다. 정리 도구는 `scripts/clean_model_cache.py`(`huggingface_hub` 삭제 전략, 기본 dry-run). 호스트의 비교용 캐시는 헤드가 사용자 위임으로 지웠다고 알려 와 1.5B(2.9GB)만 남은 것으로 본다(이번 세션에서 직접 확인한 것은 `scan_cache_dir`로 읽은 1.5B 2.9GB와 `~/openit-gpu` 15GB다).

## 7차 갱신 (D61): 기반 모델 비교 중단
- 사용자 확인으로 후보 비교를 중단했다(목표는 작동하는 파이프라인). kanana·14B는 받지 않는다. 호스트에 비교 작업은 남아 있지 않음을 확인했다. 부분 결과(qwen2.5-7b 제로샷·few-shot)와 도구, 라이선스 조사는 `docs/base-model-comparison.md`에 참고 자료로 남겼다.
- (8차에서 교체됨) 용량 보호: df 점검과 .sh 정리 스크립트. 호스트의 Qwen3-8B 미완성 조각(12GB)과 Qwen2.5-7B 캐시(15GB)는 지우지 않았고 크기와 정리 명령만 runbook에 적었다.

## 6차 갱신: 호스트 재복구 후 few-shot 실행 중 다시 끊김
- 점검 결과와 중단 지점은 docs/base-model-comparison.md의 "2차 시도 기록". 호스트에는 few-shot 작업이 남아 있을 수 있다(`base-compare-fewshot.log`, `runs-base-compare/`). 다음에 닿으면 먼저 그 상태를 확인하고(이어받기 가능), 완료된 후보의 `*-fewshot2.evaluation-report.json`을 가져와 표를 채운다.

## 간헐 실패 시험 식별·수정
- 식별: `pytest -rf` 반복에서 `tests/test_service.py::test_i3_server_conformance_with_real_client`가 가끔 실패했다(`PERMISSION_DENIED`를 기대했는데 `UNAVAILABLE`).
- 원인(확인): 서버가 읽기 토큰의 게시를 본문을 읽기 전에 403으로 거절했다. 클라이언트가 아직 본문을 보내는 중이면 연결이 초기화되어 `UNAVAILABLE`이 된다(경쟁 조건). 시험만의 문제가 아니라 실제 서버의 결함이었다.
- 수정: 오류로 답하기 전에 요청 본문을 비우면서 읽는다(`_drain`, 상한 `MODEL_REGISTRY_MAX_BODY`). 회귀 시험 `test_rejected_upload_still_returns_real_error_not_connection_reset`(40MB 본문 8회 x 2 경우).

## 5차 갱신 (D60 기반 모델 비교) — 부분 측정, 호스트가 다시 꺼짐
- 호스트 복구 뒤 qwen2.5-7b 제로샷만 측정(스키마 유효율 0/50, 메모리 5.9GB). 다음 후보 내려받는 중 호스트 불가(재시도 2회 실패). few-shot 판 설정과 도구를 추가했으나 실행하지 못했다. 자세한 내용과 이어서 하는 방법은 docs/base-model-comparison.md.
- 아래는 이전 기록(첫 불가 때).
- gpu-dev SSH가 3회(처음 + 재시도 2회) 모두 시간 초과(`Connection timed out`)라 측정하지 못했다. 규칙대로 멈췄다. 후보 4개의 라이선스·구조는 모델 카드에서 읽어 `docs/base-model-comparison.md`에 적었다(EXAONE은 NC 라이선스와 원격 코드 실행 필요로, gemma-2-9b-it·Llama-3.1-8B는 접근 제한으로 제외·미확인).
- 비교 스크립트와 기반 모델 백엔드를 준비했고 가짜 백엔드로 경로를 시험했다(시험 82건 통과). `hf_base` 백엔드는 CPU 환경에 torch가 없어 시험하지 못했다.
- 남은 일: 호스트가 켜지면 문서 맨 아래 "실행 방법"으로 측정하고 표와 추천을 채운다.

## 4차 갱신 (D53, GPU 실행 안 함)
- boundary-contracts v1.3.0으로 올림. 평가 결과를 역할별 `EvaluationReport`로 방출해 기존 `publish`의 eval_report로 올린다(METRICS.md 이름, 슬라이스 SEEN_UNSEEN·QUESTION_TYPE·UNIT 등, SAFETY 지표 포함, 임계치 없음). 못 구하는 지표는 생략하고 notes에 적었다(금지 표현률, 지연). 회귀 평가는 `reviewed_input` 우선. 시험 80건 통과, 이미지 스모크 통과(라벨 v1.3.0).
- 확인하지 못한 것: 실제 release-gate가 이 보고서를 읽는지(게이트 쪽 통합), GPU 호스트에서 v1.3.0 환경 재시험(이번엔 GPU 실행 안 함), 실제 모델 출력에서의 지표 값(시험은 오라클 모델).
- 해석 제안(CR-L006): `regression_fixed/broken`의 정의, `ungrounded_number_rate`·`slot_coverage`의 계산 방법.

## 3차 갱신 (D52, GPU 실행 안 함)
- 데이터셋 파일 저장소 독립 읽기 구현 `model_lab.file_dataset_store`(I2 레이아웃, Protocol, 해시 재확인). 시험용 gpu-dev 설정은 `gpu-dev-fixture-*.yaml`로 이름을 바꿨고 실제 설정은 `configs/gpu-dev.yaml`.
- 회귀 평가(I7, 실제 `HttpFailureStore` + 작은 서버 대역으로 시험). 개발의 데이터 이동·SSH 역터널 절차는 runbook(운영 네트워크 경계는 가정).
- 시험 75건 통과. 확인하지 못한 것: 실제 synthetic-data 출력의 `meta.json` 형식(정수 `seq`를 가정하고 없으면 created_at으로 대체), 실제 ops-feedback의 trace에 `parser_input`/`generator_input`이 들어 있는지, 실제 스테이션 네트워크.

## 2차 갱신 (헤드 결정 반영)
- boundary-contracts v1.2.0, model-runtime v0.4.0으로 올림(requirements, 라벨). base_model.id는 원래 이름(`Qwen/Qwen2.5-1.5B-Instruct`)과 해석된 커밋 해시.
- 서버 적합성 시험: 실제 `HttpModelRegistry`로 게시(tar) -> 조회 -> 어댑터 내려받기 -> 해시 검증, 200/201/409/403 (`tests/test_service.py`). 시험 65건 통과(CPU와 GPU 호스트 모두).
- 게시 장애(`UNAVAILABLE`)는 학습·평가·패키징 결과를 남기고 게시만 재시도함을 시험으로 확인(종료 코드 3).
- GPU 한 바퀴(gpu-dev): 호스트에서 model-registry 서버를 임시 포트로 띄우고 `registry.kind: http`로 게시, 클라이언트로 두 패키지를 조회·어댑터 해시 검증까지 통과, 같은 명령 재실행은 `skip`. 서버는 내렸다.
- 정답 예시 `is_fallback`은 비교하지 않음. 패키지 `ext."model-lab"`에 `resumed`·`resumed_from_step`, 보고서 `context.evaluation_backend`.
- 남은 일 추가: 큰 스테이션에서 같은 vLLM 경로(model-runtime 0.4.0 백엔드)로 한 번 더 평가한다(CR-L003). 실제 I2는 synthetic-data의 `FileDatasetStore` 팩토리로 설정한다(CR-L004).
- 아래 1차 기록 중 "재개 결정성", "클라이언트 없음", "v0.3.0 고정"은 이 갱신으로 처리·대체되었다(재개 해시 차이는 D51로 받아들임).


## 한 일
- 설계·경계·런북·변경 이력, 변경 요청 CR-L001~L005.
- 파이프라인 전체(설정 -> I2 -> 변환 -> 학습 -> 평가 -> 패키징 -> I3 게시), `run`/`run-once`/`status`, 체크포인트 재개, run 기록.
- I3 파일·메모리 구현과 HTTP 서비스(model-registry), 같은 적합성 시험(memory/file/http), Dockerfile, 스모크.
- 평가기(보호 전·후, 쪼개 보기, 보지 못한 문항 세트 따로, 임계치 없음), golden 평가 시험.
- 시험 61건 통과(`.venv/bin/python -m pytest -q`). GPU 호스트에서도 같은 시험 통과(40건 시점, 태그 wheel 설치 환경).
- **GPU 실행(gpu-dev, RTX 4070 SUPER 12GB)**: 호스트에 pip가 생긴 뒤 `configs/gpu-dev-fixture-smoke.yaml`로 학습(Qwen2.5-1.5B-Instruct, 4비트, LoRA r=16, 3스텝) -> 평가(4건) -> 패키징 -> 게시(호스트의 시험용 레지스트리)가 끝까지 돌았다. 최대 GPU 메모리 약 4.2GB. 같은 설정을 두 번 돌려 어댑터·평가 보고서 해시가 일치(eager 어텐션). 체크포인트 재개: 학습 중 프로세스를 죽이고 같은 명령을 다시 실행해 step 2에서 이어 끝까지 완료·게시. 학습 품질은 보지 않았다(3스텝, 파서 출력은 대부분 형식 위반으로 보호 적용).

## 확인하지 못한 것 / 한계
- 재개한 학습이 중단 없는 학습과 같은 어댑터 해시인지: 다르게 나왔고 원인(재개 vs 환경)을 분리하지 못했다(design.md 4절).
- `boundary_contracts.clients`(HTTP 클라이언트)는 아직 없어 대역으로만 시험했다(CR-L005).
- 서빙 백엔드(제약 디코딩)로 평가하지 않았다. 평가 백엔드는 transformers+peft다(CR-L003).
- 이 환경(CPU)에서 `hf` 학습기·백엔드 단위 시험은 없다(torch 없음). GPU 실행으로만 확인했다.
- GPU 결과를 이쪽 레지스트리로 옮겨 게시하는 명령은 만들지 않았다. 호스트에서 `registry.kind: http`로 바로 게시하는 방식(D49)을 쓴다. 이전 GPU 실행 산출물은 `runs/gpu/`(gitignore)에 있다.
- model-runtime v0.4.0(vLLM 백엔드, 어댑터 사전 검사)으로 올리지 않았다(v0.3.0 고정). 올릴 때 릴리스의 `model_runtime_version`이 바뀐다.
- 시험 데이터는 golden에서 만든 것이라 학습·평가 수치는 의미가 없다. 같은 입력에 다른 정답이 있는 예시 3그룹(CR-L001).
- 평가셋 구성·통과 임계치·지표 선정(O11)은 하지 않았다. 회귀 평가(I7), 턴 시뮬레이터, I1·I4 연동은 만들지 않았다.

## 남은 일
- v1.1.1 클라이언트가 나오면 `registry.kind: http`로 원격 게시를 시험하고 태그를 올린다.
- 실제 I2 SDK 진입점(CR-L004), 실제 데이터셋으로 실행.
- 기반 모델 확정 후 스테이션 프로파일 추가(`profiles/`), `checks.max_lora_rank`를 엔진 프로파일과 맞춘다.
- 재개 결정성 분리 시험, GPU 호스트용 배치(run-once 스케줄 등록은 deploy).

## 막힌 것
없음.
