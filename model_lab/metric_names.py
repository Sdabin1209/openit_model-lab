"""평가 지표 이름 상수 한 곳. 지표의 정본은 boundary-contracts의 지표 정의(METRICS.md, 곧 기계가 읽는 레지스트리)이고 여기는 이 코드가 내는 이름만 모은다.
시험은 이 상수를 참조한다(이름을 시험에 다시 적지 않는다)."""
from __future__ import annotations

# 폐기된 v1 지표 이름(새 보고서에 쓰면 안 된다). 왜: 계약이 지표 체계를 v2로 바꿨다(ADR 0011).
DEPRECATED_V1 = frozenset({"risk_signal_recall", "risk_signal_false_alarm", "intent_accuracy", "item_precision", "item_recall",
                           "target_accuracy", "risk_notice_verbatim_rate", "slot_coverage"})
DEPRECATED_V1_PREFIXES = ("risk_signal",)

# 학습 서버 score_a_v2.py / eval_lora_b_v2.py 이름 중 계약 레지스트리(METRICS.md v2)의 같은 계산 지표와 1:1인 것. 왜: 지표 이름의 정본은 계약
# 레지스트리이고 같은 분자·분모를 다른 이름으로 또 내면 안 된다(ML-01). 보고서에는 오른쪽 레지스트리 이름만 낸다.
# 근거: 레지스트리 doc.meaning과 evaluate.a_rates / metrics.metrics_for의 분자·분모 대조(분모 같음).
SERVER_TO_REGISTRY = {
    "schema_ok_rate": "schema_valid_raw",        # 둘 다 출력 보호 전 스키마 통과율(raw_valid / rows)
    "action_acc": "action_accuracy",
    "next_item_acc": "next_item_accuracy",
    "value_acc": "value_exact_match",            # 값이 정답과 정확히 같은 칸 / 정답 칸
    "status_acc": "status_accuracy",
    "targets_acc": "targets_match",
    "precision_acc": "precision_match",
}
# 레지스트리에 대응이 없는 이 영역의 보조 지표. 최상위 metrics에 내지 않고 ext."model-lab".detail.unregistered_metrics에 둔다.
# 레지스트리 추가 요청: docs/change-requests/CR-L011-metric-registry-additions.md (헤드가 받기 전에는 최상위에 올리지 않는다).
UNREGISTERED_A = frozenset({"action_reason_acc", "cell_acc", "save_acc", "db_row_match_rate", "exact_match_rate", "oos_f1", "key_found_rate"})
UNREGISTERED_B = frozenset({"ends_ok_rate", "asks_next_rate", "sentence_count_violation_rate", "json_leak_rate", "exact_match_rate"})
