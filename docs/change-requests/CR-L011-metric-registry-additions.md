# CR-L011: 지표 레지스트리에 없는 model-lab 지표 이름 (ML-01)

- 상태: 헤드에게 보낸다(2026-10-28, model-lab, 작업 지시서 1의 A).
- 배경: 평가 보고서의 최상위 `metrics`는 `boundary_contracts.metrics()` 레지스트리(METRICS.md)의 ACTIVE 이름만 낸다. 보고서를 만들 때 `metric_name_problems`를 부르고 위반이면 `ValueError`로 게시하지 않는다(`model_lab/metrics.py` `evaluation_report`).

## 레지스트리 이름으로 대응시킨 것 (같은 분자·분모; 이름을 새로 만들지 않았다)
| 옛 이름(학습 서버 score_a_v2) | 레지스트리 이름 | 근거 |
| --- | --- | --- |
| `schema_ok_rate` | `schema_valid_raw` | 둘 다 `raw_valid`/행 수 |
| `action_acc` | `action_accuracy` | `action`/행 수 |
| `next_item_acc` | `next_item_accuracy` | `next_item`/행 수 |
| `value_acc` | `value_exact_match` | `value`/정답 칸 수 |
| `status_acc` | `status_accuracy` | `status`/정답 칸 수 |
| `targets_acc` | `targets_match` | `targets`/targets 행 수 |
| `precision_acc` | `precision_match` | `precision`/정답 칸 수 |

## 레지스트리에 대응이 없는 것 (레지스트리에 추가 요청)
지금은 최상위에 내지 않고 `EvaluationReport.ext."model-lab".detail.unregistered_metrics`에 둔다(학습 서버 평가 값과 대조하려는 보조 값).

LoRA-A: `action_reason_acc`(action과 reason이 모두 같은 행/행 수; 레지스트리 `reason_accuracy`는 reason 있는 행만 분모라 다르다), `cell_acc`(칸의 value·status·save가 모두 같은 비율), `save_acc`, `key_found_rate`, `db_row_match_rate`, `exact_match_rate`(출력 전체 일치), `oos_precision`/`oos_recall`/`oos_f1`, `note_presence_acc`, 건수 `extra_keys`·`dup_keys`·`targets_extra_rows`·`output_invalid`.
LoRA-B: `ends_ok_rate`, `asks_next_rate`, `sentence_count_violation_rate`, `json_leak_rate`, `exact_match_rate`.

요청: 위 중 게이트가 비교에 쓸 것은 레지스트리에 추가(그룹·단위·방향 포함)하고, 쓰지 않을 것은 `ext`에 두는 것을 확정해 달라. 정하면 model-lab이 따른다. 임의 이름을 만들지 않는다.

## 요청 2: "평가하지 않음" 표현
`package-adapters`는 평가 없이 패키징해 보고서를 만든다. `EvaluationReport.metrics`는 `minItems: 1`이고 이전에 쓰던 `evaluated`(n=0)는 레지스트리에 없다. 임시로 `schema_valid_raw`를 `value=0.0, n=0`(n=0은 계산 못함)으로 낸다(`model_lab/attach.py`). 평가하지 않은 보고서를 표현하는 방법(예: 레지스트리에 `evaluated` 추가 또는 `minItems` 완화)을 정해 달라.
