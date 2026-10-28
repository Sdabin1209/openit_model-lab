"""평가 지표 v2(METRICS.md 2.1.0, D94·D96·D97), 회귀 TurnTrace 키(D98), I2 HTTP 전환(D100). 가짜 모델(oracle_backend)만 쓴다(ADR-0008)."""
import copy
import json
import os
from pathlib import Path

import pytest
from boundary_contracts import BoundaryError, validator
from turn_engine import DATASET_V2_FLOW

from model_lab import datasets, factories, metrics, pipeline, regression
from model_lab.config import ConfigError, load_config
from model_lab.metric_names import DEPRECATED_V1
from model_lab.evaluate import build_report, score_a, semantic_violations
from model_lab.text_rules import check_response, material_coverage
from tests.doubles.dataset_server import DatasetServer
from tests.doubles.fixtures import load_samples
from tests.test_evaluate_v2 import QS, session_for, with_qs

V1_NAMES = DEPRECATED_V1


def mget(ms, name, kind=None, value=None):
    for m in ms:
        sl = m.get("slice")
        if m["name"] == name and ((kind is None and sl is None) or (sl and (sl["kind"], sl["value"]) == (kind, value))):
            return m


def build(role, tmp_path, **kw):
    A, B = with_qs(load_samples("LORA_A")), with_qs(load_samples("LORA_B"))
    session = session_for({"LORA_A": A, "LORA_B": B}, tmp_path, **kw)
    ss = A if role == "LORA_A" else B
    _, pairs = build_report(role, session, ss, seen_sets={("qs", "1")}, eval_files=[], return_records=True)
    return pairs


# ---- (1) 지표 v2 ----------------------------------------------------------------------------------------------------------------

def test_a_v2_metrics_with_oracle_model(tmp_path):
    ms, notes = metrics.build_metrics("LORA_A", build("LORA_A", tmp_path), {("qs", "1")}, constrained_decoding=False)
    for n in ("value_precision", "value_recall", "precision_match", "targets_match", "value_exact_match", "action_accuracy", "reason_accuracy",
              "status_accuracy", "next_item_accuracy", "schema_valid_raw"):
        assert mget(ms, n)["value"] == 1.0, n
    # 장난감 픽스처(tests/doubles/fixtures.py)의 next_item은 실제 문항 순서표와 달라 위반이 있다. 지표가 검증기 결과와 같은 값을 내는지를 본다.
    recs = [r for _, r in build("LORA_A", tmp_path)]
    viol = [r["violations"] for r in recs if r["violations"] is not None]
    sv = mget(ms, "semantic_violation_rate")
    assert sv["n"] == len(viol) and sv["value"] == sum(1 for v in viol if v) / len(viol) and sv["value"] > 0
    assert mget(ms, "semantic_violation_rate", "VIOLATION_CODE", "SKIP_ORDER_MISMATCH")["value"] == sum("SKIP_ORDER_MISMATCH" in v for v in viol) / len(viol)
    assert mget(ms, "output_invalid_rate")["value"] == 0.0
    assert mget(ms, "semantic_violation_rate")["higher_is_better"] is False
    assert mget(ms, "output_attempts_mean")["unit"] == "count"
    for code in ("SAVE_RULE", "OUTPUT_SCHEMA"):
        assert mget(ms, "semantic_violation_rate", "VIOLATION_CODE", code)["value"] == 0.0
    assert mget(ms, "schema_valid_raw", "CONSTRAINED_DECODING", "off")["n"] > 0 and not mget(ms, "schema_valid_raw", "CONSTRAINED_DECODING", "on")
    assert mget(ms, "next_item_accuracy", "ACTION", "REDIRECT")["n"] > 0          # action별 분모
    assert not V1_NAMES & {m["name"] for m in ms}
    assert any("폐기" in n for n in notes)


def test_a_metrics_pass_the_report_schema(tmp_path):
    ms, notes = metrics.build_metrics("LORA_A", build("LORA_A", tmp_path), {("qs", "1")})
    mb, nb = metrics.build_metrics("LORA_B", build("LORA_B", tmp_path), {("qs", "1")})
    for m in (ms, mb):
        assert all(x["n"] > 0 for x in m)
    props = validator("EvaluationReport").schema["properties"]["metrics"]
    import jsonschema
    jsonschema.validate(ms, props, cls=jsonschema.Draft202012Validator)
    jsonschema.validate(mb, props, cls=jsonschema.Draft202012Validator)


def test_invalid_outputs_go_to_output_invalid_rate_not_to_semantic_rate(tmp_path):
    ms, _ = metrics.build_metrics("LORA_A", build("LORA_A", tmp_path, corrupt_every=3), {("qs", "1")})
    assert mget(ms, "output_invalid_rate")["value"] > 0 and mget(ms, "schema_valid_raw")["value"] < 1
    sv = mget(ms, "semantic_violation_rate")
    assert sv["n"] < mget(ms, "action_accuracy")["n"]                             # 형식 실패 행은 분모에서 빠진다
    assert mget(ms, "value_recall")["value"] < 1                                  # 형식 실패 행의 정답 칸은 recall 분모에 있다


def gold(**over):
    g = {"values": [{"key": "Q3.answer", "value": 1, "precision": "exact", "status": "ANSWERED", "save": True}], "action": "NEXT", "next_item": "Q4"}
    g.update(over)
    return g


def test_value_precision_recall_counts_key_value_status():
    g = gold(values=[{"key": "Q3.answer", "value": 1, "precision": "exact", "status": "ANSWERED", "save": True},
                     {"key": "Q4.answer", "value": 2, "precision": "exact", "status": "ANSWERED", "save": True}])
    p = copy.deepcopy(g)
    p["values"][1]["value"] = 3                                                   # 키만 맞고 값이 틀림 -> 맞은 것이 아니다
    p["values"].append({"key": "Q5.answer", "value": 1, "precision": "exact", "status": "ANSWERED", "save": True})   # 군더더기 -> precision만 깎인다
    c = score_a(g, p)
    assert (c["value_tp"], c["pred_values"], c["gold_keys"]) == (1, 3, 2) and c["key_found"] == 2
    q = copy.deepcopy(g); q["values"][0]["status"] = "CORRECTION"                 # 상태가 다르면 맞은 것이 아니다
    assert score_a(g, q)["value_tp"] == 1
    assert score_a(g, None).get("value_tp", 0) == 0 and score_a(g, None)["gold_keys"] == 2


def test_reason_accuracy_denominator_is_rows_with_gold_reason():
    g = gold(action="REASK", reason="MISSING", next_item="Q3")
    assert score_a(g, dict(g))["reason_rows"] == 1 and score_a(g, dict(g, reason="UNCLEAR"))["reason_ok"] == 0
    assert score_a(gold(), gold())["reason_rows"] == 0


def test_semantic_violation_detects_what_validate_extraction_detects():
    inp = {"question_id": "Q3", "current_question": "q", "user_utterance": "네", "state": {}}
    inp = next(s["input"] for s in load_samples("LORA_A")) if validator("ActionExtractionInput").is_valid(inp) is False else inp
    out = next(s["expected"] for s in load_samples("LORA_A"))
    assert semantic_violations(inp, out) == []
    bad = copy.deepcopy(out); bad["values"][0]["save"] = not bad["values"][0]["save"]
    assert "SAVE_RULE" in semantic_violations(inp, bad)
    assert semantic_violations(inp, "문자열") is None and semantic_violations({"x": 1}, out) is None     # 검사할 수 없으면 None


def synthetic_gold():
    """문항 순서표(DATASET_V2_FLOW) 31개 문항 x 답 True/False로 만든 합성 정답 A 샘플(규칙을 지키는 정답)."""
    base = load_samples("LORA_A")[0]["input"]
    out = []
    for item in DATASET_V2_FLOW.order:
        for val in (True, False):
            inp = {**base, "question_id": item, "state": {}}
            slot = f"{item}.answer"
            nxt = DATASET_V2_FLOW.next_item(item, {slot: val})
            exp = {"values": [{"key": slot, "value": val, "precision": "exact", "status": "ANSWERED", "save": True}], "action": "NEXT", "next_item": nxt}
            if validator("ActionExtractionInput").is_valid(inp) and validator("ActionExtraction").is_valid(exp):
                out.append({"sample_id": f"syn-{item}-{val}", "input": inp, "expected": exp})
    return out


def a_samples_from_env():
    p = os.environ.get("MODEL_LAB_A_SAMPLES")        # 학습 데이터 A 출력 JSONL(ActionExtractionSample, 전수 시험용)
    return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()] if p else None


def test_reference_gold_outputs_have_zero_violations():
    """D97 기준 시험: 정답 A 출력 전수에서 위반 0. MODEL_LAB_A_SAMPLES가 있으면 학습 데이터 전수, 없으면 합성 정답(180 시나리오)."""
    ss = a_samples_from_env()
    real = ss is not None
    ss = ss if real else synthetic_gold()
    bad = [(s["sample_id"], v) for s in ss if (v := semantic_violations(s["input"], s["expected"])) != []]
    assert not bad, f"위반 {len(bad)}건(검증기나 데이터 문제): {bad[:3]}"
    assert len(ss) >= (100 if real else 50)
    print("reference:", "env 데이터" if real else "합성", len(ss))


def test_b_metrics_materials_and_numbers(tmp_path):
    ms, _ = metrics.build_metrics("LORA_B", build("LORA_B", tmp_path), {("qs", "1")})
    assert mget(ms, "material_coverage")["value"] >= 0.9 and mget(ms, "material_coverage")["group"] == "ACCURACY"
    assert mget(ms, "ungrounded_number_rate")["value"] == 0.0 and mget(ms, "ungrounded_number_rate")["group"] == "SAFETY"
    assert mget(ms, "response_length_chars_p95")["value"] > 0
    assert mget(ms, "material_coverage", "ACTION", "NEXT") is not None


def test_ungrounded_number_uses_materials_only():
    req = {"action": "NEXT", "topic": "음주 3회", "user_utterance": "한 잔요", "known": "하루 1잔", "next": {"type": "NEXT", "question_id": "Q8_1", "text": "일주일에 며칠 드세요?"}}
    assert check_response(req, "하루 1잔이시군요. 일주일에 며칠 드세요?")["new_number"] is False
    assert check_response(req, "하루 3잔이시군요. 일주일에 며칠 드세요?")["new_number"] is True     # topic의 숫자는 재료가 아니다


def test_material_coverage_by_action():
    req = {"action": "REASK", "topic": "t", "user_utterance": "u", "known": "", "reason": "MISSING", "ask": "하루에 몇 잔 드시는지", "next": {"type": "SAME", "question_id": "Q7_1", "text": "x"}}
    assert material_coverage(req, "하루에 몇 잔 드시는지 알려 주세요.") is True
    assert material_coverage(req, "잘 모르겠어요.") is False
    assert material_coverage({"action": "NEXT", "topic": "t", "user_utterance": "u", "known": "", "next": {"type": "END"}}, "수고하셨습니다.") is None


# ---- (2) TurnTrace 키(D98) ------------------------------------------------------------------------------------------------------

def test_regression_reads_d98_trace_keys():
    assert regression.TRACE_KEYS == {"LORA_A": {"input": "extract_input", "output": "extract_output"},
                                     "LORA_B": {"input": "response_input", "output": "response_output"}}

    class St:
        def __init__(self, trace): self.trace = trace
        def list_cases(self, **kw): return [self.case], None
        def get_trace(self, ref): return self.trace

    A, B = load_samples("LORA_A")[0], load_samples("LORA_B")[0]
    for kind, s, key, old in (("LORA_A", A, "extract_input", "parser_input"), ("LORA_B", B, "response_input", "respond_input")):
        case = {"case_id": "c1", "version": 1, "expected_kind": kind, "expected": s["expected"], "failure_type": "OTHER",
                "source_release_id": "r1", "deidentified_trace_ref": "t1", "review_status": "CONFIRMED"}
        st = St({key: s["input"]}); st.case = case
        got = regression.build_set(st, None)
        assert len(got["samples"][kind]) == 1 and got["samples"][kind][0]["input_source"] == "trace"
        st = St({old: s["input"]}); st.case = case                                  # 옛 키·추정 키는 읽지 않는다
        assert regression.build_set(st, None)["skipped"]["trace_input_missing"]["count"] == 1


# ---- (3) I2 HTTP(D100) ----------------------------------------------------------------------------------------------------------

def test_config_accepts_http_dataset_store_and_builds_client(cfg, monkeypatch, tmp_path):
    import yaml
    raw = {k: v for k, v in cfg.items() if k != "_base_dir"}
    raw["inputs"] = {**raw["inputs"], "dataset_store": {"kind": "http", "base_url_env": "T_DS_URL", "token_env": "T_DS_TOKEN", "timeout": 30}}
    p = tmp_path / "c.yaml"; p.write_text(yaml.safe_dump(raw))
    c = load_config(p)
    seen = {}

    class Fake:
        def __init__(self, **kw): seen.update(kw)
    monkeypatch.setattr(factories, "load_callable", lambda path: Fake if path.endswith("HttpDatasetStore") else None)
    monkeypatch.setenv("T_DS_URL", "http://x:1"); monkeypatch.setenv("T_DS_TOKEN", "tok")
    assert isinstance(pipeline.make_dataset_store(c), Fake)
    assert seen == {"base_url": "http://x:1", "token": "tok", "timeout": 30}
    bad = copy.deepcopy(raw); bad["inputs"]["dataset_store"] = {"kind": "http"}
    p.write_text(yaml.safe_dump(bad))
    with pytest.raises(ConfigError):
        pipeline.make_dataset_store(load_config(p))


def test_real_config_gpu_dev_uses_http_dataset_store():
    import yaml
    c = yaml.safe_load((Path(__file__).resolve().parents[1] / "configs" / "gpu-dev.yaml").read_text())
    assert c["inputs"]["dataset_store"]["kind"] == "http"


def test_fetch_dataset_over_http_client(fixture_store, tmp_path):
    clients = pytest.importorskip("boundary_contracts.clients")
    if not hasattr(clients, "HttpDatasetStore"):
        pytest.skip("boundary-contracts에 I2 HttpDatasetStore가 아직 없다(I2 HttpDatasetStore를 내는 버전 필요)")
    from tests.doubles.dataset_store import DirDatasetStore
    srv = DatasetServer(DirDatasetStore(str(fixture_store)), ["fx-golden"])
    try:
        store = clients.HttpDatasetStore(base_url=srv.url, token="dt")
        assert store.list_versions("fx-golden") == ["v1"]
        m = datasets.fetch_dataset(store, "fx-golden", "v1", tmp_path / "d", contract_version=1)
        assert all((tmp_path / "d" / f["path"]).is_file() for f in m["files"])
        with pytest.raises(BoundaryError) as e:
            store.get_manifest("fx-golden", "v9")
        assert e.value.code == "NOT_FOUND"
        with pytest.raises(BoundaryError) as e:
            clients.HttpDatasetStore(base_url=srv.url, token="wrong").list_versions("fx-golden")
        assert e.value.code == "PERMISSION_DENIED"
    finally:
        srv.stop()
