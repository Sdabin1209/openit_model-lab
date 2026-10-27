# model-lab 설계

표시: **[확인]** 계약·브리프에서 읽었거나 시험으로 확인한 것, **[제안]** 이 문서의 제안, **[미정]** 정해지지 않은 것.

## 1. 목표

성능이 아니라 **설정 파일 하나로 반복 실행되는 파이프라인**이다(D38, D39). 입력은 데이터셋 버전과 학습 설정이고, 출력은 역할별 불변 모델 패키지(I3)다. [확인: 브리프 4절]

```
설정(+프로파일) --> 1 load_dataset(I2) --> 2 transform(render_prompt) --> 3 train --> 4 evaluate --> 5 package --> 6 publish(I3)
```

## 2. 컴포넌트 (`model_lab/`)

| 모듈 | 책임 |
| --- | --- |
| `config.py` | YAML/JSON 설정 읽기, 프로파일 병합, 형식 검증(알 수 없는 키 거부), `config_hash` |
| `datasets.py`, `schemas.py` | I2로 읽기, 파일 해시·DatasetManifest·샘플(ParserSample/GeneratorSample + 문진 스키마 input/expected) 검증, 보지 못한 문항 세트 판정, 평가 누수 검사 |
| `transform.py` | 샘플 -> (`render_prompt` 프롬프트, 정답 글자). 서버 messages <-> 샘플 변환기 포함 |
| `trainers/` | `Trainer` 인터페이스, `hf`(transformers+peft LoRA), `dry-run`(CPU 시험용) |
| `backends/` | 평가용 `ConstraintBackend`: `hf`(어댑터를 붙인 transformers), `fake-oracle`(CPU 시험용) |
| `evaluate.py` | 서빙과 같은 경로로 평가하고 보고서(쪼개 보기, 보지 못한 문항 세트 따로)를 만든다 |
| `packager.py` | ModelPackageManifest 만들기 |
| `registry.py` | I3 ModelRegistry의 파일·메모리 구현(이 영역 소유) |
| `service/` | model-registry 서비스: 파일 레지스트리를 HTTP(HTTP.md I3)로 낸다. 토큰 분리, tar 안전 해제, `/healthz`·`/readyz` |
| `factories.py` | 이웃 읽기 SDK·원격 레지스트리를 설정의 팩토리로 만든다(`*_env`는 환경 변수) |
| `pipeline.py` | 단계 실행, 재개, run 기록, `run_once` |
| `cli.py` | `model-lab run | run-once | status --config <파일>` |

## 3. 설정 스키마 [확인: `config.py`의 `CONFIG_SCHEMA`가 정본]

```yaml
config_version: 1
profile: ../profiles/gpu-dev-12g.yaml   # 선택. 설정 파일의 값이 프로파일을 덮어쓴다(객체는 재귀 병합)
run:      {name, seed, runs_dir}
inputs:   {schema_dir, dataset: {id, version?}, dataset_store: {factory: "pkg.mod:fn", args}}
base_model: {id, revision}
training:  {trainer: hf|dry-run, quantization, lora, max_seq_len, per_device_batch_size, grad_accum_steps,
            learning_rate, warmup_ratio, epochs, max_steps, max_train_samples, gpu_count, gpu_memory_fraction,
            gradient_checkpointing, checkpoint_every_steps, log_every_steps, dtype, deterministic}
roles:    {LORA_A: {package_id, training?}, LORA_B: {package_id, training?}}   # 역할별 training은 공통을 덮어쓴다
serving:  {LORA_A: <ServingConfig>, LORA_B: <ServingConfig>}                   # 형식은 model-runtime이 정한다
evaluation: {backend: {kind: hf|fake-oracle}, split: EVAL|VALIDATION, limit_samples, max_listed_failures}
registry: {kind: file|http|factory, path | base_url, token_env, timeout | factory, args}
checks:   {max_lora_rank}                                                   # 대상 엔진 프로파일의 상한. 게시 전 어댑터 r과 대조
publish:  {enabled}
```

- `version`을 생략하면 데이터셋의 가장 최근 버전이다(`list_versions()`의 마지막, 오래된 것부터). `run-once`가 쓰는 방식이다.
- **통과 임계치, 합격 기준, 지표 선택에 해당하는 키는 스키마에 없다.** 알 수 없는 키는 오류이므로 넣을 수도 없다(O11). [확인: `test_config_rejects_unknown_keys_and_thresholds`]
- 12GB에 맞춘 값(기반 모델, 4비트 양자화, 최대 길이, 배치, 메모리 비율)은 `profiles/gpu-dev-12g.yaml`에만 있다. 코드에는 모델 이름도 크기 값도 없다. 다른 스테이션은 다른 프로파일을 쓴다.
- 서빙 설정은 설정 파일의 `serving:`에서 역할별로 받아 `validate_serving_config`로 검증하고, 같은 값을 학습 데이터 변환(`render_prompt`), 평가(`load`), 매니페스트에 쓴다. 한 곳에서만 정해지므로 어긋나지 않는다.

## 4. 재현성과 재개

- `run_id = "run-" + sha256({config_hash, 데이터셋 ref(id, version, content_hash), seed, 라이브러리 버전})[7:23]`. 같은 입력은 같은 `runs/<run_id>/`를 가리킨다. `config_hash`는 위치(runs_dir, registry 경로, schema_dir, 실행 이름)를 뺀 해석된 설정의 해시다. [확인: 시험]
- `runs/<run_id>/run.json`이 설정(프로파일 병합 후), 시드, 데이터셋 ref, 라이브러리 버전(boundary-contracts, turn-engine, model-runtime, model-lab)과 환경(torch 등), 단계별 상태·해시·결과를 기록한다. 단계가 `done`이고 산출물 해시가 맞으면 건너뛴다.
- 학습은 `work/<ROLE>/`의 체크포인트(`hf`: 어댑터+옵티마이저+스케줄러+스텝, 마지막에 `state.json`을 써서 완전한 것만 인정)에서 이어 한다. 데이터 순서는 (시드, 에폭)으로 정해져 재개해도 같다.
- 시각은 run 기록에만 있다. 패키지의 `created_at`은 처음 정한 값을 run 기록에 고정해, 같은 run을 다시 돌려도 같은 매니페스트(같은 `content_hash`)가 나온다. 레지스트리 게시는 멱등이다.
- **재현성 측정 [확인: gpu-dev RTX 4070 SUPER, STATUS.md]**: 데이터·설정·`run_id`·변환 결과·dry-run 어댑터는 비트 단위로 같다. GPU 학습은 `attn_implementation: sdpa`(기본)에서는 같은 시드로도 어댑터가 달랐고, `eager` + `deterministic: true`에서 같은 설정을 두 번 돌린 어댑터·평가 보고서 해시가 일치했다(양자화 없음/4비트 둘 다 `scripts/determinism_probe.py`, 스모크 실행 2회). 그래서 프로파일은 `eager`를 쓴다. 단 **체크포인트에서 재개한 학습이 중단 없는 학습과 같은 해시인지는 확인하지 못했다**(10스텝 시험에서 달랐고, 재개하지 않은 역할도 그 두 실행 사이에 달라 원인이 재개인지 환경인지 분리하지 못했다). 한계: 다른 GPU·드라이버·라이브러리 버전에서의 일치는 약속하지 않는다.
- 이미 끝난 run을 다시 학습하지 않는 것이 재현성의 기본 장치이고, 같은 `(package_id, version)`에 다른 어댑터를 게시하려 하면 레지스트리가 `CONFLICT`로 막는다. 실험 비교는 `ext."model-lab".train_data_hash`와 `config_hash`로 한다.

## 5. 학습 데이터 변환 [확인: 브리프, I9]

- 프롬프트는 `model_runtime.render_prompt(role, input, serving_config=…, system_prompt=…, schema_dir=…)`(v0.7.1, prompt-4)다. 서빙과 같은 함수·같은 `serving_config`·설정의 `roles.<역할>.system_prompt_file`.
- 학습 목표(completion, 2.0.0): LoRA-A는 `ActionExtraction`의 키 정렬 JSON, LoRA-B는 응답 문장 그대로(JSON 아님). 서버 학습 데이터의 assistant 글자와 같다. 시스템 프롬프트는 학습이 끝나면 어댑터 디렉터리에 넣고(adapter_hash에 포함) 평가와 패키징이 그 파일을 쓴다.
- [미해결] 서빙 프롬프트의 user 글자(JCS, 공백 없음)와 서버 학습 데이터의 user 글자(공백 있음, state 순서 보존)가 다르다. 이 파이프라인으로 학습하면 서빙과 일치하지만 서버에서 학습한 어댑터와는 다르다(CR-L008).
- 학습 파일에는 TRAIN만, 검증 파일에는 VALIDATION만 들어간다. EVAL 샘플은 변환 단계에서 따로 저장하고, 학습·검증과 `scenario_id`·`sample_id`를 공유하면 단계가 중단된다(I2도 확인하지만 다시 본다, 브리프 4절).
- 같은 프롬프트에 다른 정답이 있는 학습 샘플 수를 `conflicting_train_prompts`로 기록한다(중단하지 않는다, CR-L001).
- 문항 정의에 의존하는 표현을 만들지 않는다: 입력(`awaiting_questions` 등)이 프롬프트에 그대로 들어가고 모델은 그것을 읽는다. 문항별 토큰·ID를 따로 학습하지 않는다(D32). 이 설계가 보지 못한 문항에 일반화하는지는 평가의 `unseen_question_sets` 구역으로 본다.

## 6. 평가 [확인: 구현·시험]

- 모델 세션은 `model_runtime.load(...)`로 만든다(역할 둘 모두). LoRA-A는 샘플마다 `extract_raw`(보호가 없다, D75: 형식 실패 = 서빙의 503 OUTPUT_INVALID), LoRA-B는 `respond_raw`(보호 전)와 `respond`(보호 후, 대체 문장 `is_fallback`), `last_call_info`를 기록한다. 발화·출력 원문은 보고서에 넣지 않는다(샘플 ID와 오류 이름만).
- 보고서 구조(역할마다 JSON, `eval_report_ref`로 레지스트리에 저장; `report_version: 2`):
  - `notice`: 임계치·합격 기준이 없다는 안내(O11)
  - `context`: run_id, 데이터셋 ref, adapter_hash, serving_config_hash, 라이브러리 버전, 평가 백엔드
  - `eval_files`: 평가 파일별 문항 세트와 `unseen`(매니페스트의 `files[].question_sets` 기준)
  - `overall`, `seen_question_sets`, **`unseen_question_sets`**(문항 세트 목록과 집계; D32)
  - `slices`: 차원별 집계
  - `failures`: 건수와 일부 목록
- 집계가 담는 계산값(후보이며 선정된 지표가 아니다): `n`, `raw_valid`(형식 유효), `prot_exact`(정답과 완전 일치), `outcomes`(A: ok/invalid, B: ok/fallback), `attempts` 분포, `protections` 건수, `rates`; A는 학습 서버 score_a_v2.py와 같은 계수(`counts`)와 비율, B는 `fallback` 건수, 응답 길이 평균, 문장 규칙 위반 건수(`rule_violations`).
- 쪼개 보기(D13): LoRA-A = `question_set`, `seen`, `action`(정답), `reason`(정답), `question_id`(입력 문항). LoRA-B = `question_set`, `seen`, `action`, `reason`, `next_type`. 문항에 별도의 "유형" 필드는 문진 스키마에 없어 `slot_kind`로 대신했다. [제안]
- 본/보지 못한 문항: 샘플의 `question_set`(서버 데이터에는 없어 파이프라인이 파일의 문항 세트가 하나일 때 채운다. 여럿이면 '미상')이 학습·검증 파일의 문항 세트 합집합(파일별 `question_sets`, 없으면 매니페스트 전체)에 없으면 보지 못한 문항이다.
- 평가 백엔드는 설정으로 고른다(`hf` = transformers+peft, `fake-oracle` = 정답을 그대로 내는 CPU 시험용). 제약 디코딩 백엔드는 model-runtime에 아직 없다(CR-L003).
- 회귀 평가(I7): `evaluation.regression`으로 켜고 끈다. 확정된(CONFIRMED) 정답 있는 FailureCase의 비식별 trace 입력으로 후보를 평가하고 보고서 `regression` 절에 값을 담는다(건너뛴 사례와 사유 포함, 임계치 없음). 턴 시뮬레이터는 만들지 않았다.

### 6-1. EvaluationReport (boundary-contracts v2.0.0, D53) [확인: `tests/test_evaluation_report.py`, `tests/test_evaluate_v2.py`]
레지스트리에 `eval_report`로 올라가는 바이트는 역할마다 `EvaluationReport`(JCS JSON, 스키마 검증 후)다. 스키마는 계약 그대로다. 지표 이름:
- LoRA-A(학습 서버 `score_a_v2.py`와 같은 이름과 규칙): `action_acc`, `action_reason_acc`, `next_item_acc`, `key_found_rate`, `cell_acc`(value·status·save 모두 일치), `value_acc`, `status_acc`, `save_acc`, `precision_acc`, `db_row_match_rate`, `targets_acc`, `note_presence_acc`, `exact_match_rate`, `oos_precision/recall/f1`, `extra_keys`·`dup_keys`·`targets_extra_rows`(count), PROTECTION `schema_ok_rate`·`output_invalid`(count). 형식 실패 행도 분모에 들어간다.
- LoRA-B: ACCURACY `ends_ok_rate`(끝맺음), `asks_next_rate`(다음 질문), `sentence_count_violation_rate`, `exact_match_rate`; SAFETY `forbidden_expression_rate`(금지 표현), `ungrounded_number_rate`(새 숫자); PROTECTION `json_leak_rate`, `fallback_rate`, `schema_valid_raw`, `protection_intervened`(count). 문장 규칙 구현은 `text_rules.py`(학습 서버 `eval_lora_b_v2.py`의 항목을 이 리포 방식으로).
- 공통: `coverage_seen_unseen_gap`, `regression_fixed/broken`. v1의 `risk_signal_*`, `item_*`, `intent_accuracy`, `slot_coverage`, `risk_notice_verbatim_rate`는 없앴다(v2 출력에 대응이 없다. 위험 신호의 v2 표현은 미정).
슬라이스: `SEEN_UNSEEN`, `QUESTION_SET`, `ACTION`, `REASON`, `NEXT_TYPE`(B). 합격 판정·임계치는 없다. `METRICS.md`(계약, 제안)는 아직 v1 기준이라 이 이름과 어긋난다(헤드·학습 담당 몫).

## 7. 패키징 [확인]

역할마다 ModelPackageManifest 하나: `adapter_hash = directory_hash(어댑터)`, `serving_config`와 `serving_config_hash`(model-runtime의 `serving_config_hash`), `lineage.dataset`(ArtifactRef), `lineage.training_run_id = run_id`, `lineage.question_sets`(그 역할의 TRAIN·VALIDATION 파일이 쓴 문항 세트), 라이브러리 버전, `eval_report_ref`(`eval-<package_id>.<version>`). `version = <데이터셋 버전>-<run_key 앞 8자>`. 부가 정보는 `ext."model-lab"`(run_id, config_hash, seed, 학습기, 학습 데이터 해시, 보고서 해시, 기반 모델 원래 이름). 쓰기 전에 `validator("ModelPackageManifest")`로 검증한다. `base_model.id`의 `/`는 `--`로 바꾼다(CR-L002).

## 8. I3 ModelRegistry [확인: `tests/test_registry.py`, 파일·메모리 구현이 같은 시험]

CONVENTIONS 4절 멱등·CONFLICT, 게시 전 스키마·`content_hash`·`adapter_hash`·`serving_config_hash` 검증, `list_ids`(처음 게시된 순서)·`list_versions`(오래된 것부터), `open_adapter`(복사 후 해시 재계산, 다르면 INTEGRITY_ERROR), `get_eval_report`(NOT_FOUND). 파일 구현은 매니페스트를 마지막에 쓰는 순서와 파일 잠금으로 중단·동시 게시에 안전하다.

## 8-1. 게시 대상과 서비스 (D49)

게시 단계는 `registry` 설정으로 로컬 파일 레지스트리 또는 원격 model-registry(HTTP)를 고른다. 원격은 `kind: http`이고 `boundary_contracts.clients.HttpModelRegistry`(v1.2.0)를 쓴다. 게시가 `UNAVAILABLE`로 실패해도 학습·평가·패키징 결과는 run 디렉터리에 남고 run 기록의 단계 상태가 `done`이라, 같은 명령을 다시 실행하면 게시만 다시 한다(시험: `test_registry_outage_keeps_training_result_and_retries_only_publish`). 패키지의 `ext."model-lab"`에 `resumed`, `resumed_from_step`을 남기고(D51), 평가 보고서 `context.evaluation_backend`에 평가 백엔드(이름·버전·제약 디코딩 여부)를 남긴다. 서버는 같은 파일 레지스트리를 HTTP로 낼 뿐이라 규칙(멱등·CONFLICT·해시 검증)이 한 곳에 있다. 서비스 쪽도 클라이언트와 같은 적합성 시험(`tests/test_registry.py`의 `http` 매개변수)을 통과한다. 이웃 읽기(I1·I2·I7)도 `inputs.*`의 팩토리 명세로 HTTP 클라이언트를 고른다.

## 9. 자동 실행 (D39)

`model-lab run-once --config <설정>`: 최신 데이터셋 버전으로 run을 정한다. 같은 run이 이미 끝났으면 아무것도 하지 않고(`skip`), 아니면 실행(새 버전이면 새 run, 중간에 멈춘 run이면 이어서). 종료 코드 0 = 끝났거나 할 일 없음, 2 = 오류(재시도 무의미), 3 = `UNAVAILABLE` 등 재시도할 수 있는 경계 오류. 언제 돌릴지는 deploy의 스케줄러가 정한다. 이 영역은 다른 영역을 깨우지 않는다. 학습 시행착오는 설정 파일(프로파일) 변경으로 한다.

## 10. 열린 항목

| 항목 | 상태 |
| --- | --- |
| 통과 임계치·지표·평가셋 구성(O11) | 사용자 결정. 코드·설정에 없음 |
| 실제 I2 SDK 진입점 | CR-L004 |
| 서빙 백엔드로 평가(제약 디코딩) | CR-L003 |
| 기반 모델 실제 선정, 스테이션용 프로파일 | 헤드. `profiles/`에 추가하면 된다 |
| I1(문항 읽기), I4(판정) 연동 | 이 단계에서 쓰지 않는다. (I7 회귀 케이스는 위 "회귀 평가(I7)" 항목에서 구현됨.) 문항 세트 ref는 데이터셋 매니페스트에서 얻는다 |
| 비식별되지 않은 발화 검사 | 데이터셋 쪽 책임(I2 게시 조건). 이 영역은 확인하지 못한다 |
