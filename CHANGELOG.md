# 변경 이력

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
