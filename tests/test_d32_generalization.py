"""규칙 D32: 모델은 처음 보는 문항도 다뤄야 하므로, 평가 보고서는 보지 못한 문항 세트에서의 성능을 따로 낸다.
이 파일은 그 일반화 지표 coverage_seen_unseen_gap(METRICS)이 게시되는 평가 보고서에 들어가는 것을 보증한다."""
from tests.conftest import FIXED
from tests.test_evaluation_report import metric, published
from model_lab.wiring.pipeline import Run


def test_published_report_has_coverage_seen_unseen_gap_and_unseen_slice(cfg):
    r = Run(cfg, clock=FIXED, log=lambda s: None); rec = r.run()
    _, rep = published(r, rec, "LORA_A")
    gap = metric(rep, "coverage_seen_unseen_gap")
    assert gap["group"] == "COVERAGE" and gap["n"] > 0 and "slice" not in gap
    assert isinstance(gap["value"], (int, float)) and -1.0 <= gap["value"] <= 1.0
    seen = metric(rep, "action_accuracy", "SEEN_UNSEEN", "seen")["n"]
    unseen = metric(rep, "action_accuracy", "SEEN_UNSEEN", "unseen")["n"]
    assert seen > 0 and unseen > 0                                   # 보지 못한 문항을 따로 센다(D32)
    assert any("coverage_seen_unseen_gap" in n for n in rep["notes"])  # 값을 어떻게 읽는지 보고서에 적혀 있다
