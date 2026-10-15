# CR-L001 golden 예시에서 관찰한 차이 (수신: 헤드)

상태: 제안. 분류 기준은 `tests/golden/README.md`의 (a) 구현 결함 (b) 해석 차이 (c) 예시 오류. 아래는 `tests/test_golden.py`가 확인한 것이다.

## 1. 모든 생성기 예시의 `is_fallback`이 true  [(c) 예시 오류로 분류, 확인함]
- 생성기 예시 221건 모두 `GeneratorOutput.is_fallback == true`인데 `response_text`는 정상 문장이다(예: A1 "담배는 피우시나요?").
- 서빙은 모델이 낸 정상 출력에 `is_fallback=false`를 채운다(I9, D36). 예시대로면 정상 출력이 전부 대체 출력이 된다.
- model-lab은 `response_text`만 비교하고 `is_fallback`은 비교 기준으로 쓰지 않는다. 보고서에는 `fallback_matches_expected`로 건수만 남는다(현재 0/221).
- 요청: golden의 `is_fallback`을 실제 의도에 맞게 고치거나, 정적 예시에서 `is_fallback`은 비교 대상이 아니라고 `tests/golden/README.md`에 적는다.

## 2. 같은 입력에 서로 다른 정답이 있는 파서 예시  [(c) 예시 오류 또는 (b) 해석 차이, 원인은 확인하지 못함]
`render_prompt`로 만든 프롬프트가 같은데 정답(ParserOutput)이 다른 그룹이 3개 있다. 학습 데이터에 섞이면 라벨이 충돌한다.
- J1, J2, J7a, J7b, J7c, J8c, J8d, J8e, J8f, J8h, N14, R15 (정답 4종)
- K9d, R16 (2종)
- S4b, S4c (2종)
J 계열은 모델 출력이 깨진 경우(J7 등)를 다루는 사례로 보이며, 입력은 같고 기대 출력이 "보호 뒤의 값"이라 입력만으로는 구분되지 않을 수 있다. 이것은 추정이며 확인하지 못했다.
- 파이프라인은 이를 중단하지 않고 run 기록의 `conflicting_train_prompts`로 센다(데이터셋 품질 정보). 데이터셋(synthetic-data)에도 같은 충돌이 생길 수 있다.
- 요청: 위 사례의 입력/기대 출력을 확인해 주기 바란다(입력이 실제로 같은지, 기대 출력이 모델 출력인지 보호 뒤 값인지).

## 3. 같은 입력·출력이 다른 사례에 중복  [참고]
같은 프롬프트·같은 정답이 여러 사례에 반복된다(28개 그룹, 예: A4a/A4b/F4/G7의 생성기). 충돌이 아니며 문제 없다.
