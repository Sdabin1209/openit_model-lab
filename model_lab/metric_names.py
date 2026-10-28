"""평가 지표 이름 상수 한 곳. 지표의 정본은 boundary-contracts의 지표 정의(METRICS.md, 곧 기계가 읽는 레지스트리)이고 여기는 이 코드가 내는 이름만 모은다.
시험은 이 상수를 참조한다(이름을 시험에 다시 적지 않는다)."""
from __future__ import annotations

# 폐기된 v1 지표 이름(새 보고서에 쓰면 안 된다). 왜: 계약이 지표 체계를 v2로 바꿨다(ADR 0011).
DEPRECATED_V1 = frozenset({"risk_signal_recall", "risk_signal_false_alarm", "intent_accuracy", "item_precision", "item_recall",
                           "target_accuracy", "risk_notice_verbatim_rate", "slot_coverage"})
DEPRECATED_V1_PREFIXES = ("risk_signal",)

# LoRA-A / LoRA-B 보고서가 반드시 내는 이름(학습 서버 score_a_v2.py, eval_lora_b_v2.py 호환)
A_REQUIRED = frozenset({"schema_ok_rate", "action_acc", "action_reason_acc", "next_item_acc", "cell_acc", "value_acc", "status_acc",
                        "save_acc", "db_row_match_rate", "targets_acc", "exact_match_rate", "oos_f1", "key_found_rate", "precision_acc"})
B_REQUIRED = frozenset({"ends_ok_rate", "asks_next_rate", "ungrounded_number_rate", "forbidden_expression_rate",
                        "sentence_count_violation_rate", "json_leak_rate", "fallback_rate", "schema_valid_raw", "exact_match_rate"})
