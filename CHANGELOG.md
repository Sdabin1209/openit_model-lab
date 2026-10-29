# 변경 이력

## 미배포 - U42: 빌드 진입점 + 계약 v2.5.0 (ADR 0024)
- `scripts/build-image.sh`(BuildImageRequest/Result). 계약 v2.5.1 핀. 서빙 설정은 설정을 읽을 때 계약 `ServingConfig`로 한 번 검증하고 패키저·파이프라인의 `model_runtime.validate_serving_config` 호출을 지웠다(R7).

## 미배포 - 열차 D115: 계약 v2.4.1, model-runtime v0.10.0 (코드 변경 없음)
- 핀만 바꿨다(requirements.txt, requirements.lock, requirements-service.txt). 새로 만드는 매니페스트의 pipeline_schema_version은 "2.4", lineage.model_runtime_version은 "0.10.0"이 된다(상수에서 읽음). 시험·이미지 smoke 통과.

## 미배포 - 계약 v2.3.0 핀 (v2.2.2에서 올림; 코드 변경 없음)
- requirements.txt, requirements.lock, requirements-service.txt. 시험·이미지 smoke 통과.

## 미배포 - 단위 4a: model-registry를 WSGI 앱 + waitress로 (D111, ADR 0023)
- 직접 만든 스레드 HTTP 서버(연결 세마포어, 소켓 timeout, 본문 버리기)를 지우고 waitress 설정으로 옮겼다. `MODEL_REGISTRY_THREADS` 추가.
- 동작 변경: 본문을 다 받은 뒤 앱이 불린다(비인증 요청도 본문 상한까지 받는다), 상한 초과는 413(waitress 본문), 연결 상한 초과는 503 없이 대기. ADR 0023 결과 절.
- 계약 핀 v2.2.2(413/414/431을 클라이언트가 INVALID_RECORD로 읽음).
- 새 의존 `waitress==3.0.2`(requirements-service.txt, requirements.lock, Dockerfile).

## 미배포 - 단위 3: 계약 v2.2.1, turn-engine 코드 의존 제거, 계약 스키마 사용 (헤드 지시 D104·D107·D109, ADR 0022)
- 핀: boundary-contracts v2.2.1, model-runtime v0.9.0, openit-survey-schema survey-schema-v1.1.0. turn-engine 제거.
- `semantic_violations`/`semantic_violation_rate`: 계약 `check_action_extraction` + 문진 스키마 `question_flow`. 스키마를 어긴 출력에서는 `OUTPUT_SCHEMA`가 더 나오지 않는다(R7). 보고서 notes 문구 변경.
- `ModelPackageLayout` 검사(게시·패키지 생성), 회귀 trace `OpsFeedbackDeidentifiedTrace` 검증(`trace_invalid`), 설정 `lineage.turn_engine_version`.
- 모의 학습기 어댑터 파일 이름 `adapter_model.dryrun` -> `adapter_model.safetensors`. 스모크의 revision 폴백 제거.

## 미배포 - 계약 v2.2.0 열차 핀 (헤드 지시 wo-bc220)
- 핀만 바꿨다: boundary-contracts v2.1.1 -> v2.2.0(`requirements.txt`, `requirements.lock`, `requirements-service.txt`), turn-engine v0.4.2 -> v0.4.3, model-runtime v0.8.0 -> v0.8.1(`requirements.txt`, `requirements.lock`). 잠금 파일은 태그만 담고 커밋 해시가 없다. `pipeline_schema_version`은 코드·시험 모두 계약 상수 `PIPELINE_SCHEMA_VERSION`에서 읽어 바꿀 곳이 없었다. 계약 변경점은 boundary-contracts CHANGELOG 2.2.0절이 정본이다.

## 미배포 - model-runtime v0.7.7 -> v0.8.0 (헤드 지시 wo7)
- `requirements.txt`, `requirements.lock`의 model-runtime 핀만 올렸다(다른 라이브러리 핀은 그대로). v0.8.0의 호환 깨짐(호출별 시간 상한 환경 변수, 인증 실패 401, 릴리스 매니페스트 `model_runtime_version` MAJOR.MINOR 일치)은 `model-runtime/docs/CHANGELOG.md`가 정본이다. 이 리포는 `model_runtime_version`을 `model_runtime.RUNTIME_VERSION`에서 읽으므로 코드 변경은 없다.

## 0.3.0 (제안; 태그 없음) - 지표 v2, TurnTrace 키, I2 HTTP (D94·D96~D98·D100)
- 평가 지표를 METRICS.md v2로: `value_precision/recall`, `action/reason/status/next_item_accuracy`, `semantic_violation_rate`(turn-engine `validate_extraction`, 위반 코드별 slice), `output_invalid_rate`, `material_coverage`, 재료 기준 `ungrounded_number_rate`, `response_length_chars_p95`. v1 지표 폐기. 기존 score_a_v2 이름은 보조로 유지. ADR 0011. `unit=chars`는 스키마에 없어 count로 냄(CR-L009).
- 회귀 평가의 trace 입력 키를 `extract_input`/`response_input`으로 확정(추정 키 제거, D98).
- `inputs.dataset_store`에 `kind: http` 추가, `gpu-dev.yaml`을 HTTP로 전환(ADR 0012). 시험 195건 통과, 1건 건너뜀(계약 2.1.0 클라이언트 대기).

## 0.2.12 (제안; 태그 없음) - 3차 점검 반영
- 시작 때 토큰 강도 검사(32자 이상, 서로 다른 글자 8개 이상, 미달이면 시작 거부, 값은 메시지에 없음). 실패 제한의 의미(로그·CPU 보호, 방어는 엔트로피)를 ADR 0009·boundary.md에 적음. 스모크는 무작위 토큰을 쓴다.
- keep-alive 연결에서 요청별 상태(`_consumed`, `_sent_close`)를 요청마다 되돌림(앞 요청의 상태가 다음 거절의 본문 버리기를 건너뛰게 하던 문제). 1MiB 넘는 업로드 거절의 연결 초기화는 한계로 문서화.

## 0.2.11 (제안; 태그 없음) - 재점검 반영 (ML-1, ML-2, L)
- 인증 실패 제한: 토큰 검증을 먼저 하고 유효한 토큰은 제한과 무관하게 통과(제한은 유효하지 않은 토큰을 보낸 요청에만; 유효한 읽기 토큰의 쓰기 403은 횟수에 넣지 않음).
- 우아한 종료: SIGTERM이면 Connection: close, 유휴 keep-alive 연결 즉시 닫기, 진행 중 요청만 `MODEL_REGISTRY_SHUTDOWN_GRACE`(8초) 대기. 유휴 연결 1개로 30초 걸리던 종료가 수 초 안에 끝난다.
- 거절 응답은 본문을 최대 1MiB만 버리고 연결을 닫는다. `recover()`는 게시와 같은 잠금 안에서 돈다. 시험 추가.

## 0.2.10 (제안; 태그 없음) - model-runtime v0.7.3
- model-runtime 태그를 v0.7.2 -> v0.7.3으로 올림(requirements.txt, requirements.lock, README, docs/design.md, docs/runbook.md, docs/boundary.md, scripts/compare_base_models.py). 이 영역의 시험·스모크는 model-runtime 서버를 띄우지 않고 라이브러리(render_prompt, load, 가짜 백엔드)만 쓴다. v0.7.3의 서버 기본값 변경(토큰 없으면 시작 거부 등)은 이 영역에 영향이 없다(model-runtime CHANGELOG는 읽지 못했고 코드 검색으로만 확인). 과거 이력(0.2.7)은 그대로 둔다.

## 0.2.9 (제안; 태그 없음) - 전수 점검 지적 반영 (ML-1~6, ML-12)
- ML-3: 게시 중단 뒤 재시도해도 `list_versions`에 버전이 한 번만 나온다(order에 이미 있으면 추가하지 않고, 읽을 때 중복 제거). 서비스는 시작 때 `.tmp`와 게시 잔해를 지우고 SIGTERM에서 진행 중 요청을 마치고 멈춘다.
- ML-1: 서버 소켓 timeout, 동시 연결 상한, 동시 게시 1건, 같은 주소의 인증 실패 제한(모두 503 UNAVAILABLE, 환경 변수로 조정).
- ML-2: 게시 전 여유 공간 검사, 본문 tar 삭제와 하드 링크 저장으로 최대 사용량 약 3배에서 약 2배, 학습 전 점검과 doctor의 디스크 정책(`output.disk_policy` block 기본), runbook의 df 서술 정정.
- ML-5: `package-adapters --train-record-a/-b`로 학습 기록을 대조해 매니페스트 `ext."model-lab".lineage`에 넣고, 없으면 경고.
- ML-6: 게시자 기록(역할·주소·시각)을 매니페스트 옆 `.published.json`에 남김. 서명은 하지 않음(제안만).
- ML-12: 학습 설정 `server.script_sha256`과 `train-doctor`/`train`의 서버 스크립트 해시 비교(서버 접속 없음, 읽히는 환경에서만). ML-14는 문서만.
- 시험 18건 추가(`tests/test_hardening.py`).

## 0.2.8 (제안; 태그 없음) - 기반 모델 비교 시험의 호스트 디스크 의존 제거
- `scripts/compare_base_models.py`: 사용량 예산 가드(`footprint_guard`)를 실제 백엔드(`--backend hf`)에서만 적용한다. 가짜 백엔드는 내려받지 않는데도 후보당 예상 크기를 더해, gpu-dev처럼 HF 캐시와 작업 폴더가 큰 호스트에서 시험이 실패했다. `main(argv)`로 인자를 주입할 수 있게 했다.
- 시험 2건 추가: 작업 폴더가 예산보다 크게 보여도(희소 파일, HOME/HF_HOME을 tmp로 돌림) 가짜 백엔드는 통과하고, `hf` 백엔드는 여전히 가드로 멈춘다. 같은 종류(호스트 상태 의존) 시험을 tests/에서 훑었으나 더 찾지 못했다(`test_train_wrap`의 디스크 점검은 `min_free_gb=0`, 캐시는 경고뿐).

## 0.2.7 (제안; 태그 없음) - model-runtime v0.7.2
- model-runtime 태그를 v0.7.1 -> v0.7.2로 올림(가짜 A에 정정(CORRECT) 출력을 더한 하위 호환 변경; requirements.txt, requirements.lock, README, docs/runbook.md, docs/design.md, docs/boundary.md, scripts/compare_base_models.py). turn-engine v0.4.0, boundary-contracts v2.0.2는 그대로. 과거 이력(CHANGELOG 0.2.4, docs/STATUS.md)은 그대로 둔다.

## 0.2.6 (제안; 태그 없음) - 학습 서버 인자 이름 채움 (G06)
- `server.arg_map`을 채웠다: 헤드가 2026-10-27에 서버 `train_lora_a.py`/`train_lora_b.py`의 `add_argument`를 읽어 확인한 이름이다(`--model --data_dir --prefix --out_dir --lr --epochs --batch --accum --max_len --seed --rank --alpha --dropout --eval_steps --dry_run`). 서버에 접속한 것도 `--help`를 실행한 것도 이 영역이 아니다.
- `args_verified: true`의 뜻을 정했다: "인자 이름을 서버 코드로 확인함". 실행 확인이 아니다. 근거 글을 새 필드 `server.args_evidence`에 두고 `train-doctor`가 보여 준다. 확인하지 못한 것(`--help` 출력, 타입 검사 방식, 산출물 위치, 스크립트가 데이터 해시·기반 모델 revision을 남기는지)은 설정 주석과 runbook에 미확인으로 남겼다.
- 불리언 하이퍼파라미터는 값 없는 플래그다(true면 `--dry_run`만, false면 넘기지 않음). 이전에는 `true`라는 값을 붙였다.
- `configs/train.yaml`은 A 8B v2(run_config `a_8b_v2`와 같은 값, profile qwen3-8b), 새 `configs/train-b.yaml`은 B 8B(`b_8b_1014`, 데이터 경로에 공백). 하이퍼파라미터에 `lora_dropout`, `eval_steps`, `dry_run`을 추가했다. 이전 `train.yaml`은 4B였다.
- 시험: 두 설정의 명령줄을 서버 `run_config.json` 값과 대조(타입 포함), 인자 순서 안정성, 공백 경로가 인자 하나인 것, 값 없는 플래그. out_dir은 서버가 상대 경로로 기록한 값이라 시험에서 절대 경로로 맞춰 비교했다.

## 0.2.5 (제안; 태그 없음) - 24GB급 프로파일
- `profiles/qwen3-8b.yaml` 추가(기반 모델 `Qwen/Qwen3-8B`, `max_lora_rank` 16). model-runtime `rtx4090-24g.json`의 기반 모델·max_lora_rank와 같다. 14B는 A만 학습되어 있고 24GB에 맞지 않아 만들지 않았다. 런북에 프로파일 표와 model-runtime 연결 설명, 시험 2건 추가(`tests/test_attach.py`: 8B 패키징, 4B 어댑터·rank 초과 거부와 미게시).

## 0.2.4 (제안; 태그 없음) - model-runtime v0.7.1
- model-runtime 태그를 v0.7.0 -> v0.7.1로 올림(requirements.txt, requirements.lock, README, docs/runbook.md, docs/design.md, scripts/compare_base_models.py의 설명). turn-engine v0.4.0, boundary-contracts v2.0.2는 그대로. 설정(configs)·시험에는 v0.7.0 문자열이 없어 바꾸지 않았다. 과거 이력(CHANGELOG 0.2.2, docs/STATUS.md의 옛 기록)은 그대로 둔다.

## 0.2.3 (제안; 태그 없음) - 학습 서버 정본 감싸기 (D78, G06)
- G09: v1 정적 예시 `tests/golden/`(210개)를 제거했다. 시험·코드·스크립트·Dockerfile 어디도 읽지 않는 것을 grep으로 확인했다(남은 `golden` 언급은 문자열 `fx-golden`(데이터셋 id)과 설명 글뿐).
- 근본 원인: 학습 코드가 두 곳(서버 정본, `trainers/hf.py` 사본)에 따로 있었다. 서버 코드를 복사·재구현하지 않고 정본을 호출하는 얇은 감싸기를 만들었다.
- 설정 한 개 `configs/train.yaml` + 명령 `python -m model_lab train --config configs/train.yaml [--dry-run]` + `python -m model_lab train-doctor --config ...` (`model_lab/train_wrap.py`). 서버 코드 위치·스크립트 이름·인자 이름(`server.arg_map`)·데이터셋·하이퍼파라미터는 모두 설정값이다. 비밀은 설정에 적을 수 없다(기존 규칙). dry-run은 조립한 명령과 예상 산출물 경로만 낸다. 실행 뒤에는 어댑터가 package-adapters 입력 규약(파일 두 개, 기반 모델 일치, rank 상한)과 맞는지 확인하고 `<어댑터>.train-wrap.json`에 명령·설정 해시·서버 스크립트 해시·데이터셋 파일 해시·어댑터 해시를 남긴다. 시험 `tests/test_train_wrap.py`(서버 스크립트는 대역).
- 프로파일 `profiles/qwen3-4b.yaml`(기반 모델과 max_lora_rank 16만).
- `trainers/hf.py` 처리 결정: **삭제하지 않고 비정본으로 표시**. 이유: 사용처가 있다(파이프라인 `train` 단계의 기본값, `configs/gpu-dev*.yaml`, 12GB 호스트의 파이프라인 통과 시험, `determinism_probe`). 코드 머리말과 `make_trainer("hf")`의 `UserWarning`에 "서버 정본과 다르고 같은 어댑터를 만들 수 없다"를 명시했다. 파이프라인의 train 단계를 감싸기에 연결하지는 않았다: 파이프라인은 prompt/completion 형식을 만들지만 서버 스크립트는 `{prefix}_{train,val,test}.jsonl`의 `messages`를 읽고, 서버 스크립트의 인자·산출물 위치를 아직 확인하지 못했다(학습 서버에 닿지 못함). 연결은 인자 확인 뒤 별도 단위로 한다.
- 확인하지 못한 것: 서버 스크립트의 인자 이름, 어댑터가 나오는 위치와 이름, 서버 run 기록(run.json)의 키. 이번 세션에 학습 서버 ssh가 연결 거부(4회)였다. 이전 세션이 읽은 서버 스크립트 기본값은 `configs/train.yaml` 하이퍼파라미터에 옮겼고, `args_verified: false`로 구멍 표시했다. 실제 학습(GPU)은 구멍.

## 0.2.2 (제안; 태그 없음)
- turn-engine v0.4.0(v1 폐기 함수 제거, boundary-contracts v2.0.2 고정), model-runtime v0.7.0(비밀 파일화, prompt-3 제거, RUNTIME_VERSION 0.7, serving_config 허용 템플릿 ['prompt-4'])으로 올림. 서빙 설정의 prompt_template_version을 prompt-4로 바꿈(configs, 기반 모델 비교 스크립트). requirements.txt/lock 갱신.

## 0.2.1 (제안; 태그 없음, 브랜치 train)
- boundary-contracts v2.0.2(D84). 비밀(`MODEL_REGISTRY_READ_TOKENS`, `MODEL_REGISTRY_WRITE_TOKENS`, 설정의 `token_env` 전부)을 `boundary_contracts.read_secret`로 읽는다: `NAME_FILE` 경로 우선, 없으면 `NAME` 환경 변수. 값이 로그·`/readyz`·오류에 나오지 않는 시험(`tests/test_secrets.py`). 이미지 스모크는 토큰을 파일로 준다. turn-engine·model-runtime 태그는 올리지 않았다.
- 설정 파일의 인라인 비밀 거부(`config.check_inline_secrets`, release-gate와 같은 규칙): 비밀 같은 키에 문자열 값이 직접 있으면 `ConfigError`(키 경로만, 값 없음). `*_env`는 이름 형식만 허용. 시험 `tests/test_secrets.py`.
- `model-lab package-adapters`(`model_lab/attach.py`): 학습 서버 어댑터 두 개 + 시스템 프롬프트 두 개 + 프로파일 -> 패키지 -> 게시. 기반 모델 불일치·필수 파일 누락·순위 초과는 거부. 평가는 하지 않고 보고서에 그렇게 표시한다. 런북 "학습 서버 어댑터를 얹는 절차".

## 0.2.0 (제안, 이전 중; 태그 없음)
- boundary-contracts v2.0.0(D74, D75). v1 레코드는 읽지 않는다.
- 변환기: 학습 서버 messages <-> ActionExtractionSample/ResponseSample(`transform.py`), 검증 스크립트 `scripts/check_server_dataset.py`.
- 패키징: `ModelPackageManifest.system_prompt`(어댑터 디렉터리 안 파일, adapter_hash에 포함), 설정 `roles.<역할>.system_prompt_file`(필수), `prompts/`. 레지스트리가 프롬프트 파일을 확인한다.
- 데이터셋 파일 역할 LORA_A/LORA_B, 샘플 검증은 v2 샘플 스키마.
- model-runtime v0.6.0, turn-engine v0.3.0(태그)로 올림. 서빙 설정 prompt-3 / protect-2.
- model-runtime v0.6.4로 올림. 서빙 설정 relax-2(supported_features에 pattern, minItems 추가; relax-1은 거부). 설정·시험의 relax_policy_version을 relax-2로 바꿈.
- boundary-contracts v2.0.1(ActionExtraction properties 순서만 바뀐 PATCH), model-runtime v0.6.5로 올림. 서빙 설정 relax_policy_version은 relax-3. 설정·시험·비교 스크립트의 값을 relax-3으로 바꿈.
- turn-engine v0.3.1로 올림(v0.3.0 태그가 boundary-contracts v2.0.0을 고정해 v2.0.1과 pip 해결이 불가능했다. v0.3.1은 v2.0.1).
- 파이프라인: 학습 데이터 변환은 v0.6.0 `render_prompt`(시스템 프롬프트 포함), 학습 직후 시스템 프롬프트를 어댑터 디렉터리에 넣는다(adapter_hash 포함), 평가는 `extract_raw`(A, 보호 없음) / `respond_raw`·`respond`(B). 샘플에 문항 세트가 없으면 파일의 문항 세트(하나일 때)로 채움.
- 평가·지표: A는 score_a_v2.py 이름과 규칙, B는 끝맺음·다음 질문·새 숫자·금지 표현·문장 수(`text_rules.py`). v1 지표(risk_signal_* 등) 삭제. 상세 보고서 `report_version: 2`.
- 회귀: `expected_kind` LORA_A/LORA_B, 입력은 ActionExtractionInput / ResponseRequest로 검증. trace 입력 키는 추정(미정).
- 시험: 합성 v2 fixture(tests/doubles/fixtures.py), v1 golden 시험 제거, 기반 모델 비교 스크립트를 v2로 이식(few-shot 제외). 간헐 실패하던 캐시 정리 시험 수정.
- 정리: 이전 커밋에서 pipeline.py에 중복된 코드 조각 제거.
- 미이전: 학습기(`trainers/hf.py`, 정본 결정 대기), 서빙-학습 user 글자 불일치(CR-L008).

## 0.1.0 (2026-10-15)
- 용량 보호를 df 대신 사용량 예산(max_footprint_gb)으로 교체, 캐시 정리를 huggingface_hub 삭제 전략으로(scripts/clean_model_cache.py).
- D61: 기반 모델 비교 중단(참고 자료로 보존). 내려받기 전 디스크 확인(disk_guard)과 모델 캐시 정리 스크립트(기본 dry-run) 추가.
- 수정: model-registry가 오류 응답 전에 요청 본문을 비운다(간헐적 UNAVAILABLE 경쟁 조건), 회귀 시험 추가.
- 기반 모델 후보 비교 도구(scripts/compare_base_models.py, configs/compare/base-model-candidates.yaml)와 docs/base-model-comparison.md. gpu-dev 불가로 측정은 미실행.
- EvaluationReport(boundary-contracts v1.3.0) 방출, 회귀 평가의 reviewed_input 우선, eval_report_ref를 패키지 기준으로 변경.
- 데이터셋 파일 저장소 읽기 구현, 회귀 평가(I7), 실제 실행 설정 configs/gpu-dev.yaml, 시험용 설정 이름 변경(gpu-dev-fixture-*), 개발 네트워크 절차.
- boundary-contracts v1.2.0, model-runtime v0.4.0로 올림. base_model.id 원래 이름 사용. 평가에서 is_fallback 비교 제거, 보고서에 평가 백엔드, 패키지 ext에 resumed 기록. 서버 적합성 시험을 실제 HttpModelRegistry로 수행. 게시 장애 시 게시만 재시도.
- model-registry 서비스(I3 HTTP 서버), Dockerfile, 스모크 스크립트, 원격 게시 설정(`registry.kind: http|factory`), 이웃 읽기 SDK 팩토리 구조.
- 파이프라인(설정 -> I2 읽기 -> 변환 -> 학습 -> 평가 -> 패키징 -> I3 게시), `run`/`run-once`/`status`, 체크포인트 재개, run 기록.
- I3 ModelRegistry 파일·메모리 구현과 적합성 시험.
- 평가기: 서빙과 같은 경로(model-runtime `*_raw` + 보호 적용), 차원별 쪼개 보기, 보지 못한 문항 세트 따로 보고. 임계치·지표 선정 없음(O11).
- 학습기: transformers+peft LoRA(`hf`), CPU 시험용 `dry-run`. 프로파일 `profiles/gpu-dev-12g.yaml`.
- golden 평가기 시험, 변경 요청 CR-L001~L004.
- 의존: boundary-contracts v1.1.0, turn-engine v0.2.0, model-runtime v0.3.0.
