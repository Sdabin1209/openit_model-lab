"""EvaluationReport(v1.3.0) 방출: 스키마, 지표 이름·그룹, 슬라이스, 생략 규칙, 게시, 회귀 reviewed_input 우선."""
import copy
import json

import pytest
from boundary_contracts import canonical, validator

from model_lab.pipeline import Run
from tests.conftest import FIXED
from tests.test_regression import case, failure_world, reg_cfg  # noqa: F401  (픽스처 재사용)
from tests.doubles.fixtures import load_pairs


def published(r, rec, role):
    m = r.registry.get_manifest(rec["result"]["packages"][role]["package_id"], rec["result"]["packages"][role]["version"])
    return m, json.loads(r.registry.get_eval_report(m["eval_report_ref"]))


def test_published_report_is_evaluation_report(cfg):
    r = Run(cfg, clock=FIXED, log=lambda s: None); rec = r.run()
    for role in ("LORA_A", "LORA_B"):
        m, rep = published(r, rec, role)
        validator("EvaluationReport").validate(rep)
        assert rep["role"] == role and rep["report_id"] == m["eval_report_ref"]
        assert rep["package"] == {"id": m["package_id"], "version": m["version"], "content_hash": m["content_hash"]}
        assert rep["content_hash"] == canonical.content_hash(rep) and rep["decoding"]["temperature"] == 0
        assert all(x["n"] > 0 for x in rep["metrics"])                       # n=0은 두지 않고 생략한다
        keys = json.dumps(rep, ensure_ascii=False).lower()
        assert "threshold" not in keys and '"passed"' not in keys and '"verdict"' not in keys
        assert "detail" in rep["ext"]["model-lab"]                           # 이전 쪼개 보기 보고서가 ext에 있다


def metric(rep, name, kind="ALL", value=None):
    for x in rep["metrics"]:
        sl = x.get("slice")
        if x["name"] == name and ((sl is None and kind == "ALL") or (sl and sl["kind"] == kind and sl["value"] == value)):
            return x


def test_metric_names_groups_and_slices(cfg):
    r = Run(cfg, clock=FIXED, log=lambda s: None); rec = r.run()
    _, a = published(r, rec, "LORA_A"); _, b = published(r, rec, "LORA_B")
    safety_a = {x["name"] for x in a["metrics"] if x["group"] == "SAFETY"}
    assert {"risk_signal_recall", "risk_signal_false_alarm"} <= safety_a
    assert {"item_precision", "item_recall", "target_accuracy", "intent_accuracy"} <= {x["name"] for x in a["metrics"] if x["group"] == "ACCURACY"}
    assert metric(a, "item_precision")["value"] == 1.0 and metric(a, "schema_valid_raw")["value"] == 1.0     # 오라클 모델
    assert metric(a, "risk_signal_recall")["value"] == 1.0
    kinds = {x["slice"]["kind"] for x in a["metrics"] if "slice" in x}
    assert {"SEEN_UNSEEN", "QUESTION_TYPE", "UNIT", "RANGE", "QUESTION_SET", "INTENT"} <= kinds
    assert metric(a, "item_recall", "SEEN_UNSEEN", "unseen")["n"] > 0 and metric(a, "item_recall", "SEEN_UNSEEN", "seen")["n"] > 0
    assert metric(a, "coverage_seen_unseen_gap")["group"] == "COVERAGE"
    # 생성기: 금지 표현·지연은 계산할 수 없어 생략하고 notes에 이유가 있다
    names_b = {x["name"] for x in b["metrics"]}
    assert "forbidden_expression_rate" not in names_b and "length_p95_ms" not in names_b
    assert any("forbidden_expression_rate" in n for n in b["notes"]) and any("length_p95_ms" in n for n in b["notes"])
    assert {"risk_notice_verbatim_rate", "ungrounded_number_rate", "slot_coverage"} <= names_b
    assert metric(b, "risk_notice_verbatim_rate")["value"] == 1.0
    assert not any(x["name"].startswith("regression_") for x in a["metrics"])
    assert any("regression_fixed" in n for n in a["notes"])


def test_protection_metrics_move_with_corrupt_outputs(cfg):
    c = copy.deepcopy(cfg); c["evaluation"]["backend"]["corrupt_every"] = 4
    r = Run(c, clock=FIXED, log=lambda s: None); rec = r.run()
    _, a = published(r, rec, "LORA_A")
    assert metric(a, "schema_valid_raw")["value"] < 1.0 and metric(a, "protection_intervened")["value"] > 0
    assert metric(a, "protection_intervened")["unit"] == "count" and metric(a, "fallback_rate")["higher_is_better"] is False


def test_regression_metrics_and_reviewed_input_first(cfg, failure_world, monkeypatch):
    # c-parser의 trace에는 마스킹된 입력이 없고 reviewed_input만 있다 -> reviewed_input을 쓴다
    P = [p for p in load_pairs(30) if p["role"] == "PARSER"]
    for c in failure_world.cases:
        if c["case_id"] == "c-parser":
            c["reviewed_input"] = P[0]["input"]
    failure_world.traces["tr-c-parser"] = {"parser_input": {"utterance": "깨진 입력"}}      # 쓰면 입력 계약 위반으로 건너뛰게 된다
    c = reg_cfg(cfg, failure_world, monkeypatch)
    r = Run(c, clock=FIXED, log=lambda s: None); rec = r.run()
    _, a = published(r, rec, "LORA_A")
    fixed, broken = metric(a, "regression_fixed"), metric(a, "regression_broken")
    assert fixed["group"] == "REGRESSION" and fixed["n"] == 1 and fixed["value"] == 1 and broken["value"] == 0
    detail = a["ext"]["model-lab"]["detail"]["regression"]
    assert detail["input_sources"] == {"reviewed_input": 1} and detail["skipped"]["input_invalid"]["count"] == 0 if "input_invalid" in detail["skipped"] else True


def test_evaluation_report_is_reproducible(cfg, tmp_path):
    import shutil
    a = Run(cfg, clock=FIXED, log=lambda s: None); ra = a.run()
    c2 = copy.deepcopy(cfg); c2["run"]["runs_dir"] = str(tmp_path / "r2"); c2["registry"]["path"] = str(tmp_path / "g2")
    b = Run(c2, clock=FIXED, log=lambda s: None); rb = b.run()
    assert published(a, ra, "LORA_A")[1]["content_hash"] == published(b, rb, "LORA_A")[1]["content_hash"]
