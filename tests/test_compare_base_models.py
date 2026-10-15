"""기반 모델 비교 스크립트의 경로(가짜 백엔드): 표본 선택, EvaluationReport 방출, 후보별 실패 격리."""
import json
import subprocess
import sys
from pathlib import Path

import yaml
from boundary_contracts import validator

from tests.conftest import ROOT, SCHEMA_DIR


def run(tmp_path, cands, extra=()):
    cfg = yaml.safe_load((ROOT / "configs/compare/base-model-candidates.yaml").read_text())
    cfg["candidates"] = cands
    cp = tmp_path / "c.yaml"; cp.write_text(yaml.safe_dump(cfg))
    r = subprocess.run([sys.executable, str(ROOT / "scripts/compare_base_models.py"), "--config", str(cp), "--schema-dir", str(SCHEMA_DIR),
                        "--out", str(tmp_path / "out"), "--backend", "fake", *extra], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr[-500:]
    return json.loads((tmp_path / "out/summary.json").read_text())


def test_fake_run_emits_valid_reports_and_includes_all_risk_cases(tmp_path):
    s = run(tmp_path, [{"name": "a", "id": "x/a"}, {"name": "b", "id": "x/b"}])
    assert [x["name"] for x in s] == ["a", "b"] and s[0]["samples"] == 50
    rep = json.loads((tmp_path / "out/a.evaluation-report.json").read_text())
    validator("EvaluationReport").validate(rep)
    assert rep["metrics"] and next(m for m in rep["metrics"] if m["name"] == "risk_signal_recall")["n"] == 9    # golden의 RISK_SIGNAL 사례 전부
    assert "threshold" not in json.dumps(rep).lower()


def test_sample_selection_is_deterministic():
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    m = importlib.import_module("compare_base_models")
    assert [s["sample_id"] for s in m.pick_samples(50)] == [s["sample_id"] for s in m.pick_samples(50)]


def test_disk_guard_stops_before_download(tmp_path, monkeypatch):
    import importlib
    import pytest
    sys.path.insert(0, str(ROOT / "scripts"))
    m = importlib.import_module("compare_base_models")
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    with pytest.raises(SystemExit, match="디스크 부족"):
        m.disk_guard({"id": "x/huge", "approx_gb": 10**9}, 20)                 # 남은 용량보다 큰 후보
    m.disk_guard({"id": "x/small", "approx_gb": 0.001}, 0)                      # 충분하면 통과
    done = tmp_path / "hf/hub/models--x--cached/blobs"; done.mkdir(parents=True)
    m.disk_guard({"id": "x/cached", "approx_gb": 10**9}, 20)                    # 이미 받은(미완성 조각 없음) 후보는 건너뛴다
    (done / "a.incomplete").write_text("")
    with pytest.raises(SystemExit):
        m.disk_guard({"id": "x/cached", "approx_gb": 10**9}, 20)                # 미완성이면 다시 받아야 하므로 검사한다


def test_candidate_configs_have_size_estimates():
    for f in ("base-model-candidates.yaml", "base-model-candidates-fewshot.yaml"):
        cfg = yaml.safe_load((ROOT / "configs/compare" / f).read_text())
        assert cfg["min_free_gb"] > 0 and all(c["approx_gb"] > 0 for c in cfg["candidates"])
