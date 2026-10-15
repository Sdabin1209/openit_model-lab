# CR-L006 METRICS.md(제안) 구현 해석과 생략한 지표 (수신: 헤드, 학습 담당자)

구현한 해석 [제안]. 다르면 알려 달라(지표 이름은 계약 변경 없이 바꿀 수 있다).
- `regression_fixed` = 회귀 사례(확정된 실패, 정답 있음) 중 후보가 정답과 완전 일치한 건수, `regression_broken` = 일치하지 않은 건수. 현재 활성 릴리스의 결과와 비교하지 않는다(model-lab은 I4 활성 릴리스의 출력을 모른다). "다시 틀린"의 기준이 활성 릴리스 대비라면 게이트가 `baseline`과 맞대어 판단해야 한다.
- `ungrounded_number_rate`: 보호 전 모델 출력의 아라비아 숫자 열이 응답 지시(directive) 안의 숫자 열에 없으면 해당. 한글 수사는 확인하지 못한다.
- `slot_coverage`: `say_values`·`notices`의 값 문자열이 응답에 그대로 들어 있는 샘플 비율(표현을 바꿔 말하면 미달로 센다).
- `risk_notice_verbatim_rate`: `fixed_sentences`의 `RISK_NOTICE` 문장이 모두 응답에 그대로 있는 비율(보호 후 출력 기준).
- `value_exact_match`: 문자열 완전 일치(단위 환산 전). 카탈로그의 "숫자는 단위 환산 후"는 구현하지 않았다.
- 정확도 계열은 보호 후(서빙되는) 출력, `schema_valid_raw`·`ungrounded_number_rate`는 보호 전 출력 기준.

생략한 지표 [확인: notes에 사유가 있다]
- `forbidden_expression_rate`: 금지 표현 목록이 정해지지 않았다. 목록(또는 판별 규칙)이 오면 구현한다.
- `length_p95_ms`: 평가가 지연을 재지 않는다(평가 백엔드가 서빙과 다르므로 의미도 다르다).

계약 쪽 메모: `EvaluationReport.package.content_hash`가 매니페스트의 `content_hash`이고 매니페스트가 `eval_report_ref`를 담으므로 ref를 보고서 내용에서 정하면 순환한다. 그래서 ref를 `eval-<package_id>.<version>`으로 정했다.
