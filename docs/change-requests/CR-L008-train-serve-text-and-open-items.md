# CR-L008 v2 파이프라인 이전에서 남은 결정 (수신: 헤드)

기준: boundary-contracts v2.0.0, model-runtime v0.6.0, turn-engine v0.3.0. "확인"은 이 세션에서 읽고 시험한 것.

## 1. 학습 글자와 서빙 글자가 다르다 (model-runtime, 헤드) [확인]
- 서빙: `render_prompt`(prompt-3)는 user 내용을 JCS(공백 없음, 모든 키 정렬)로 쓴다.
- 서버 학습 데이터(`dataset_10_14`): user 내용은 공백이 있는 JSON(`", "`, `": "`)이고 최상위 키만 정렬, LoRA-A `state`는 삽입 순서 보존이다(`transform.render_input`이 글자 단위로 재현, 서버 10,264줄 전부 일치).
- 결과: 서버에서 학습한 어댑터를 v0.6.0 서빙에 올리면 입력 글자가 학습과 다르다. 이 리포 파이프라인으로 학습한 어댑터는 `render_prompt`로 만든 프롬프트로 학습하므로 서빙과 같다.
- 제안(헤드·model-runtime 결정): (a) model-runtime이 서버 글자 규칙을 쓰는 템플릿(prompt-4)을 추가하거나, (b) 학습을 JCS 글자로 다시 한다. 어느 쪽인지는 학습기 정본 결정과 함께 정해야 한다 [미정].

## 2. 학습기 정본 [미정, 기존]
`trainers/hf.py`는 건드리지 않았다. 파이프라인은 `train.jsonl`을 `{sample_id, prompt, completion}`으로 쓴다(prompt = render_prompt 문자열, completion = 서버 assistant 글자). `hf` 학습기가 이 prompt를 채팅 템플릿 없이 토큰화하므로 prompt-3의 특수 토큰이 문자열로 들어간다. 토크나이저 대조는 하지 못했다 [미확인].

## 3. 위험 신호의 v2 표현 [미정, 지시서]
v1 `risk_signal_*` 지표를 없앴다. v2 A 출력에는 위험 신호가 없다(`REDIRECT/OUT_OF_SCHEMA`의 `note`가 후보일 수 있으나 확인하지 않았다). SAFETY 지표는 B의 `forbidden_expression_rate`, `ungrounded_number_rate`만 있다.

## 4. 회귀 평가의 trace 입력 키 [미정, survey-service]
v2 TurnTrace의 모델 입력 키 이름을 확인하지 못해 `extract_input`, `response_input`/`respond_input`으로 추정했다(`regression._TRACE_KEYS`). `reviewed_input`이 있는 사례는 영향이 없다.

## 5. METRICS.md [헤드]
계약의 `METRICS.md`는 v1 지표를 적고 있다. 이 리포가 방출하는 v2 이름은 `docs/design.md` 6-1에 있다. 문서 갱신은 헤드 몫이다.

## 6. turn-engine `validate_extraction` 평가 지표화 [제안]
A 출력의 의미 위반율(targets, next_item, 건너뛰기, save)을 평가 지표로 넣을 수 있다. boundary.md가 평가에서 turn-engine 직접 호출을 금지하고 있어 하지 않았다. 허락하면 `DATASET_V2_FLOW`를 입력으로 `semantic_violation_rate`를 추가한다.

## 7. 이미지 라벨 [확인]
서비스 이미지(model-registry)는 turn-engine·model-runtime을 포함하지 않아 라벨을 `n/a`로 두었다(규칙의 "기록한다"와 다르게 보일 수 있음). 학습 잡의 버전은 `requirements.lock`과 run 기록의 `libraries`에 남는다.
