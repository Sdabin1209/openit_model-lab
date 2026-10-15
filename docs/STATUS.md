# STATUS (2026-10-15)

## 5차 갱신 (D60 기반 모델 비교) — 호스트 불가
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
