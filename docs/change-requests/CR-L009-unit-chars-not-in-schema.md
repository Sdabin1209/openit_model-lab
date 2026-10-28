# CR-L009: METRICS.md의 `unit=chars`가 EvaluationReport 스키마의 `unit` 열거에 없다

- 상태: 헤드에게 보낸다(2026-10-28, model-lab).
- 확인한 것: `boundary-contracts/METRICS.md`(86da3bf)의 `response_length_chars_p95`는 `unit=chars`라고 쓰지만, `schemas/EvaluationReport.schema.json`의 `metrics[].unit`은 `["ratio","count","ms","score"]`만 허용한다. `chars`를 내면 보고서 검증이 실패한다(시험으로 확인).
- 임시 처리: model-lab은 `unit=count`로 낸다. 값은 글자 수 95백분위(nearest-rank)다.
- 요청(둘 중 하나): (a) 스키마 `unit` 열거에 `chars`를 더한다(계약 MINOR), 또는 (b) METRICS.md를 `unit=count`로 고친다. 정하면 model-lab이 따른다.
