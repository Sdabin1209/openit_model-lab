# 0011. 평가 지표를 METRICS.md v2로 바꾸고 의미 위반율에 turn-engine을 쓴다

- 상태: 채택됨 (헤드 결정 D94·D96·D97·D98을 구현). 의미 위반율의 turn-engine 의존은 [ADR 0022](0022-turn-engine-코드-의존을-계약으로-옮긴다.md)가 대체했다(계약 `check_action_extraction`).

## 맥락
v1 지표(`risk_signal_*`, `intent_accuracy`, `item_*`, `slot_coverage`)는 v2 출력(`ActionExtraction`, 문자열 응답)에 근거가 없어 계산할 수 없다. 제약 디코딩이 형식을 지켜 형식 통과율은 변별력이 낮다. 서비스는 의미 위반을 재시도한 뒤 실패 턴으로 처리한다(D75).

## 결정
- 지표 이름·정의는 `boundary-contracts/METRICS.md` v2(계약 2.1.0)를 정본으로 따른다. `[기본값]`(D96)은 학습 담당 확인 전 임시로 적용한다: `value_precision/recall`은 (key, value, status) 일치, `next_item_accuracy`는 `slice kind=ACTION`이 action별 분모, `schema_valid_raw`는 제약 디코딩 켠/끈 값을 `slice kind=CONSTRAINED_DECODING`으로 구분, 금지 표현 목록 소유자는 미정(D59).
- `semantic_violation_rate`는 turn-engine `validate_extraction`(버전은 `requirements.txt`에 태그로 고정)을 부른다. 위반 코드별 값은 `slice kind=VIOLATION_CODE`. 형식 실패 출력은 분모에서 빼고 `output_invalid_rate`가 센다. 문항 순서표는 기본 `DATASET_V2_FLOW`(학습 데이터셋 v2의 31문항·건너뛰기 7건, 시험용 고정값이라고 turn-engine이 밝힘, D99)이고 `build_report(flow=...)`로 바꾼다.
- `ungrounded_number_rate`는 런타임 검사가 아니라 평가 지표다(D94). 재료는 `known, ask, previous, current, hold, next.text, user_utterance`. `topic`·`note`는 B가 말하지 않아 뺐다.
- `material_coverage`의 action별 필수 재료 표(`text_rules._REQUIRED`)와 "말했다"의 판정(핵심 단어 1/3 이상과 숫자 포함)은 잠정이다.
- 학습 서버 `score_a_v2.py` 이름의 지표(`action_acc` 등)는 호환·대조용으로 함께 낸다. 이름은 자유 문자열이라 계약을 깨지 않는다.

## 왜
- 운영과 같은 검증기로 재야 지표가 운영 실패율과 같은 뜻이다(타당성의 표준 관행, D97).
- (key, value, status) 일치는 분류 평가의 표준 정의로, 키만 맞은 오답을 놓치지 않는다(D96).
- 백분위(`response_length_chars_p95`)는 nearest-rank: 보간이 필요 없고 실제 관측값을 낸다.
- 기준 시험: 정답 A 출력 전수에서 위반 0(`tests/test_metrics_v2.py::test_reference_gold_outputs_have_zero_violations`). 환경변수 `MODEL_LAB_A_SAMPLES`(ActionExtractionSample JSONL)가 있으면 그 전수, 없으면 문항 순서표로 만든 합성 정답 62건.

## 검토한 대안과 버리는 이유
- 평가 코드 안에 위반 규칙을 따로 구현: 서비스와 규칙이 어긋날 수 있다(D97).
- v1 지표 유지: 계산 불가 지표가 남는다(D96).
- 합격선 설정: 정하지 않는다(O11).

## 결과
- 얻는 것: 운영과 같은 규칙의 위반율, v2 이름의 보고서.
- 감수: turn-engine 의존이 평가에도 생기고 버전을 맞춰야 한다. 측정한 실제 모델 값은 없다(가짜 모델 시험만, ADR 0008). `unit=chars`는 스키마 열거에 없어 `count`로 낸다(CR-L009). 실제 학습 데이터 전수 시험은 환경변수로만 돌렸으며 이 문서를 쓴 시점에 실데이터로는 돌리지 않았다.

## 되돌리는 조건
D97이 걸어 둔 조건(충돌 규칙이 확인돼 해소되지 않으면 서비스 TurnTrace의 `validation_violations` 수집으로 대체)이 성립하면 새 ADR로 대체한다. 담당자가 `[기본값]`을 바꾸면 따른다.
