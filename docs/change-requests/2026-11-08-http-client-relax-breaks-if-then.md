# 변경 요청: 읽기 클라이언트의 `_relax`가 enum 조건(if/then)을 깨뜨린다

- 대상: boundary-contracts v4.0.2 `boundary_contracts/clients/_http.py`의 `_relax` (영역에서 확인한 것: 설치본 4.0.2)
- 증상(확인함): `HttpFailureStore.list_cases`가 계약 검증기(`validator("FailureCase")`)를 통과하는 유효한 FailureCase를 `INTEGRITY_ERROR: FailureCase 응답이 스키마를 위반한다`로 거부한다. 경로 `expected/values/0/precision`, 이유 `'unspecified' was expected`, 같은 레코드에 `False is not of type 'null'`도 나온다. `expected.values`가 비어 있지 않은 모든 ActionExtraction(시험 표본 8건 중 7건)에서 재현된다. `_reader("ActionExtraction")`만으로도 같다.
- 추정(확인하지 않음): `_relax`가 문자열 enum을 `{"type": "string"}`으로 바꾸므로 `status`의 enum을 조건(`if`)으로 쓰는 `if/then`이 항상 참이 되어 UNKNOWN·MISSING용 제약(value null, precision unspecified)이 모든 칸에 적용된다.
- 요청: 조건절(`if`/`oneOf` 판별)에 쓰인 enum은 완화하지 않거나, 완화 뒤에도 분기 판별이 유지되게 한다. 실제 레코드(ActionExtraction 표본)를 읽는 시험을 계약 쪽에 둔다.
- 영역 영향: 실패 사례 회귀 평가(I7)가 실서버 응답에서 중단된다. 영역 시험 3건(`tests/test_regression.py`, `tests/test_evaluation_report.py`의 회귀 시험)이 이 때문에 실패하며, 영역은 시험을 완화하지 않고 그대로 둔다.
