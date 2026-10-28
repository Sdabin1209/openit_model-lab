"""EvaluationReport(v2.0.0) 방출: 스키마, 지표 이름·그룹, 슬라이스, 생략 규칙, 게시, 회귀 reviewed_input 우선."""
import copy
import json

import pytest
from boundary_contracts import canonical, validator

from model_lab.metric_names import DEPRECATED_V1, SERVER_TO_REGISTRY, UNREGISTERED_A, UNREGISTERED_B, DEPRECATED_V1_PREFIXES

from model_lab.pipeline import Run
from tests.conftest import FIXED
from tests.test_regression import case, failure_world, reg_cfg  # noqa: F401  (픽스처 재사용)
from tests.doubles.fixtures import load_samples


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
    names_a = {x["name"] for x in a["metrics"]}
    # 최상위에는 계약 레지스트리 이름만 낸다(ML-01). 서버 이름 중 같은 계산은 레지스트리 이름으로, 대응 없는 것은 detail로 간다.
    assert set(SERVER_TO_REGISTRY.values()) <= names_a and not (set(SERVER_TO_REGISTRY) | UNREGISTERED_A) & names_a
    assert UNREGISTERED_A <= {x["name"] for x in a["ext"]["model-lab"]["detail"]["unregistered_metrics"]}
    assert not DEPRECATED_V1 & names_a                   # v1 지표는 없다
    for n in ("action_accuracy", "next_item_accuracy", "value_exact_match", "schema_valid_raw"):
        assert metric(a, n)["value"] == 1.0                                                            # 오라클 모델
    assert metric(a, "action_accuracy")["group"] == "ACCURACY" and metric(a, "schema_valid_raw")["group"] == "PROTECTION"
    ex = {x["name"]: x for x in a["ext"]["model-lab"]["detail"]["unregistered_metrics"] if "slice" not in x}
    assert ex["extra_keys"]["value"] == 0 and ex["extra_keys"]["higher_is_better"] is False
    kinds = {x["slice"]["kind"] for x in a["metrics"] if "slice" in x}
    assert {"SEEN_UNSEEN", "QUESTION_SET", "ACTION", "REASON"} <= kinds
    assert metric(a, "action_accuracy", "SEEN_UNSEEN", "unseen")["n"] > 0 and metric(a, "action_accuracy", "SEEN_UNSEEN", "seen")["n"] > 0
    assert metric(a, "action_accuracy", "ACTION", "REDIRECT")["n"] > 0
    assert metric(a, "coverage_seen_unseen_gap")["group"] == "COVERAGE"
    # 생성기: 끝맺음·다음 질문·새 숫자·금지 표현·문장 수
    names_b = {x["name"] for x in b["metrics"]}
    assert {"ungrounded_number_rate", "forbidden_expression_rate", "fallback_rate", "schema_valid_raw"} <= names_b and not UNREGISTERED_B & names_b
    bd = {x["name"]: x for x in b["ext"]["model-lab"]["detail"]["unregistered_metrics"] if "slice" not in x}
    assert UNREGISTERED_B <= set(bd)
    assert metric(b, "forbidden_expression_rate")["group"] == "SAFETY" and bd["exact_match_rate"]["value"] == 1.0
    assert bd["ends_ok_rate"]["value"] == 1.0 and metric(b, "fallback_rate")["value"] == 0
    assert "length_p95_ms" not in names_b and any("length_p95_ms" in n for n in b["notes"])
    assert not any(x["name"].startswith("regression_") for x in a["metrics"])
    assert any("regression_fixed" in n for n in a["notes"])
    assert any(p in n for n in a["notes"] for p in DEPRECATED_V1_PREFIXES)


def test_protection_metrics_move_with_corrupt_outputs(cfg):
    c = copy.deepcopy(cfg); c["evaluation"]["backend"]["corrupt_every"] = 4
    r = Run(c, clock=FIXED, log=lambda s: None); rec = r.run()
    _, a = published(r, rec, "LORA_A"); _, b = published(r, rec, "LORA_B")
    assert metric(a, "schema_valid_raw")["value"] < 1.0 and metric(a, "output_invalid_rate")["value"] > 0
    assert metric(a, "action_accuracy")["value"] < 1.0   # 형식 실패 행은 틀린 것으로 센다
    assert metric(b, "fallback_rate")["value"] > 0 and metric(b, "fallback_rate")["higher_is_better"] is False
    assert metric(b, "protection_intervened")["unit"] == "count"


def test_regression_metrics_and_reviewed_input_first(cfg, failure_world, monkeypatch):
    # c-parser의 trace에는 입력 계약을 어기는 입력이 있고 reviewed_input만 맞다 -> reviewed_input을 쓴다
    P = load_samples("LORA_A")
    for c in failure_world.cases:
        if c["case_id"] == "c-parser":
            c["reviewed_input"] = P[0]["input"]
    failure_world.traces["tr-c-parser"] = {"extract_input": {"user_utterance": "깨진 입력"}}      # 쓰면 입력 계약 위반으로 건너뛰게 된다
    c = reg_cfg(cfg, failure_world, monkeypatch)
    r = Run(c, clock=FIXED, log=lambda s: None); rec = r.run()
    _, a = published(r, rec, "LORA_A")
    fixed, broken = metric(a, "regression_fixed"), metric(a, "regression_broken")
    assert fixed["group"] == "REGRESSION" and fixed["n"] == 1 and fixed["value"] == 1 and broken["value"] == 0
    detail = a["ext"]["model-lab"]["detail"]["regression"]
    assert detail["input_sources"] == {"reviewed_input": 1} and "input_invalid" not in detail["skipped"]


def test_evaluation_report_is_reproducible(cfg, tmp_path):
    import shutil
    a = Run(cfg, clock=FIXED, log=lambda s: None); ra = a.run()
    c2 = copy.deepcopy(cfg); c2["run"]["runs_dir"] = str(tmp_path / "r2"); c2["registry"]["path"] = str(tmp_path / "g2")
    b = Run(c2, clock=FIXED, log=lambda s: None); rb = b.run()
    assert published(a, ra, "LORA_A")[1]["content_hash"] == published(b, rb, "LORA_A")[1]["content_hash"]


def test_report_metric_names_pass_contract_registry(cfg):
    """기대값은 계약 레지스트리(boundary_contracts.metric_name_problems, METRICS.md)에서 온다(ML-01). 이 영역이 정한 이름 목록이 아니다."""
    from boundary_contracts import metric_name_problems
    r = Run(cfg, clock=FIXED, log=lambda s: None); rec = r.run()
    for role in ("LORA_A", "LORA_B"):
        _, rep = published(r, rec, role)
        assert metric_name_problems(rep) == []


def test_evaluation_report_refuses_unregistered_metric_name():
    from model_lab import metrics as M
    with pytest.raises(ValueError, match="registry"):
        M.evaluation_report(role="LORA_A", report_id="x", created_at="2026-10-28T00:00:00Z",
                            package={"id": "p", "version": "1", "content_hash": "sha256:" + "0" * 64},
                            dataset={"id": "d", "version": "1", "content_hash": "sha256:" + "0" * 64}, question_sets=[],
                            backend={"name": "n", "version": "1"}, decoding={"temperature": 0.0, "seed": 1}, notes=[], detail={},
                            metrics=[M._m("not_in_registry_metric", "ACCURACY", 0.5, 3, True)])
