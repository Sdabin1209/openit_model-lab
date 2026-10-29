# 0022. turn-engine 코드 의존을 계약(action_rules, 문진 스키마 흐름표, 승격 스키마)으로 옮긴다

## 상태
채택 (2026-10-29, `turn_engine_version` 표지 부분은 [ADR 0027](0027-계약-3-0-0-전환과-라벨-정직성.md)이 대체, 헤드 지시 단위 3, 결정 D104·D107·D109). ADR 0011의 "의미 위반율은 turn-engine `validate_extraction`" 부분을 대체한다(나머지는 유효).

## 맥락
평가의 `semantic_violation_rate`가 turn-engine `validate_extraction`과 그 안의 `DATASET_V2_FLOW`(시험용 고정 문항 순서)를 불렀고, 패키지 계보 값(`turn_engine_version`)도 turn-engine에서 읽었다. turn-engine은 퇴역 예정이고, LoRA-A 출력 규칙은 계약의 데이터(`action_extraction_rules.json`)와 기준 검사기로 옮겨졌다(D104). 또 어댑터 디렉터리 구성과 회귀 평가가 읽는 trace는 손으로 키를 보던 것을 계약 스키마(D107)가 대신한다.

## 결정
- 위반 검사: `boundary_contracts.action_rules.check_action_extraction(input, output, flow)`. 흐름표는 문진 스키마 `question_flow`(`survey_schema.load_data`)에서 order·skips만 뽑아 계약 `QuestionFlowTable`로 검증해 쓴다(`model_lab/flow.py`). 사본을 두지 않는다. 위반 코드 목록은 계약 규칙 데이터에서 읽는다.
- 계보 값: `turn_engine_version` 필드는 계약 기록에 남아 있으므로 채운다. 값은 설정 `lineage.turn_engine_version`, 없으면 `0.4.3`(마지막 릴리스를 가리키는 고정 표지, v 없음: release-gate CONTRACT_COMPAT가 설치 패키지 버전 문자열과 전체 문자열로 비교한다). 실행한 코드에서 읽은 값이 아니다.
- 어댑터 디렉터리: 게시와 패키지 생성이 `ModelPackageLayout` 스키마로 검사한다(필수 3개 파일, 모르는 파일 거부). 모의 학습기의 어댑터 파일 이름을 `adapter_model.safetensors`로 맞췄다(내용은 safetensors가 아니다).
- 회귀 평가 trace: `OpsFeedbackDeidentifiedTrace`로 검증한다(모르는 키 거부). 어기면 `trace_invalid`로 건너뛴다. `RegressionTraceInput`은 승격되지 않았다. trace 안의 입력은 `ActionExtractionInput`/`ResponseRequest`로 따로 본다(trace 스키마는 입력 모양을 느슨하게만 본다).

## 왜
영역은 서로의 구조를 모르고 계약으로만 만난다(D104). 검사 규칙의 정본이 한 곳(계약 데이터)이면 모델 런타임, 서비스, 평가가 같은 뜻으로 잰다. 흐름표는 survey-service가 소유한 사실이라 복사하면 어긋난다.

## 검토한 대안과 버리는 이유
- turn-engine을 계속 설치: 퇴역 예정이고 규칙이 두 곳(turn-engine, 계약)에 남는다.
- 흐름표를 이 리포에 복사: 정본이 둘이 된다.
- 계보 값을 빈 문자열/null: 계약 필드가 필수 문자열이고 3.0.0 전에는 필드가 남는다.

## 결과
- 얻는 것: turn-engine 설치·import 없음(`pip uninstall turn-engine` 상태에서 전체 시험 통과). 규칙이 계약과 같이 움직인다.
- 평가 지표 값이 바뀌지 않는다는 것의 범위: `tests/test_semantic_diff.py`가 이전 구현의 기록(`tests/data/semantic_violations_before.json`, 611건)과 비교한다. 스키마를 통과한 출력(평가기가 넘기는 것은 `raw.valid`뿐)은 위반 코드가 모두 같다. 스키마를 어긴 출력에서는 이전 구현이 `OUTPUT_SCHEMA`를 추가로 냈고 지금은 내지 않는다(형식은 model-runtime이 보증, R7). 보고서의 `notes` 문구는 바뀌었다.
- 감수: 흐름표의 정본이 문진 스키마 `v1.1.0`이다(핀). `turn_engine_version`은 표지일 뿐이다.

## 되돌리는 조건
계약이 3.0.0에서 `turn_engine_version` 필드를 없애면 표지와 설정 키를 지운다. 흐름표를 설정에서 받아야 하는 요구(문항 세트별 흐름)가 생기면 `semantic_violations(flow=...)` 인자로 이미 열려 있다.
