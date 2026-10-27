"""기반 모델 비교 스크립트의 경로(가짜 백엔드): 표본 선택, EvaluationReport 방출, 후보별 실패 격리."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml
from boundary_contracts import validator

from tests.conftest import ROOT, SCHEMA_DIR


def run(tmp_path, cands, extra=(), env=None):
    cfg = yaml.safe_load((ROOT / "configs/compare/base-model-candidates.yaml").read_text())
    cfg["candidates"] = cands
    cfg["fewshot"] = 0
    cp = tmp_path / "c.yaml"; cp.write_text(yaml.safe_dump(cfg))
    r = subprocess.run([sys.executable, str(ROOT / "scripts/compare_base_models.py"), "--config", str(cp), "--schema-dir", str(SCHEMA_DIR),
                        "--out", str(tmp_path / "out"), "--backend", "fake", *extra], capture_output=True, text=True, cwd=ROOT, env=env)
    assert r.returncode == 0, r.stderr[-500:]
    return json.loads((tmp_path / "out/summary.json").read_text())


def test_fake_run_emits_valid_reports(tmp_path):
    s = run(tmp_path, [{"name": "a", "id": "x/a"}, {"name": "b", "id": "x/b"}])
    assert [x["name"] for x in s] == ["a", "b"] and s[0]["samples"] == 50
    rep = json.loads((tmp_path / "out/a.evaluation-report.json").read_text())
    validator("EvaluationReport").validate(rep)
    assert rep["metrics"] and next(m for m in rep["metrics"] if m["name"] == "action_acc")["n"] == 50
    assert "threshold" not in json.dumps(rep).lower()


def _full_workdir_env(tmp_path):
    """HOME/HF_HOME을 tmp로 돌리고 작업 폴더(~/openit-gpu)를 예산(20GB)보다 크게 보이게 한다(희소 파일이라 실제 디스크는 쓰지 않는다)."""
    import os
    (tmp_path / "home/openit-gpu").mkdir(parents=True)
    with open(tmp_path / "home/openit-gpu/big", "wb") as f:
        f.truncate(25 * 2**30)
    return {**os.environ, "HOME": str(tmp_path / "home"), "HF_HOME": str(tmp_path / "hf")}


def test_fake_run_ignores_host_disk_state(tmp_path):
    s = run(tmp_path, [{"name": "a", "id": "x/a"}], env=_full_workdir_env(tmp_path))     # 작업 폴더가 가득 차도 가짜 백엔드는 통과
    assert s[0]["samples"] == 50 and "error" not in s[0]


def test_hf_backend_still_blocked_by_footprint_guard(tmp_path, monkeypatch):
    import pytest
    m = load_script()
    env = _full_workdir_env(tmp_path)
    monkeypatch.setenv("HOME", env["HOME"]); monkeypatch.setenv("HF_HOME", env["HF_HOME"])
    cfg = yaml.safe_load((ROOT / "configs/compare/base-model-candidates.yaml").read_text())
    cfg["candidates"] = [{"name": "a", "id": "x/a", "approx_gb": 1}]
    cp = tmp_path / "c.yaml"; cp.write_text(yaml.safe_dump(cfg))
    with pytest.raises(SystemExit, match="사용량 예산 초과"):                               # 실제 백엔드는 모델을 받기 전에 멈춘다
        m.main(["--config", str(cp), "--schema-dir", str(SCHEMA_DIR), "--out", str(tmp_path / "out"), "--backend", "hf"])


def test_sample_selection_is_deterministic():
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    m = importlib.import_module("compare_base_models")
    assert [s["sample_id"] for s in m.pick_samples(50)] == [s["sample_id"] for s in m.pick_samples(50)]


def make_cache(hub, repo, size, *, incomplete=False):
    """HF 캐시 배치(blobs, snapshots 링크, refs)를 만든다."""
    commit = (hashlib.sha1(repo.encode()).hexdigest())[:40]      # 모델마다 다른 커밋(같으면 delete_revisions가 어느 모델을 지울지 정해지지 않는다)
    d = hub / ("models--" + repo.replace("/", "--"))
    (d / "blobs").mkdir(parents=True); (d / "refs").mkdir(); (d / "snapshots" / commit).mkdir(parents=True)
    (d / "blobs" / "b1").write_bytes(b"x" * size)
    (d / "snapshots" / commit / "w.bin").symlink_to(d / "blobs" / "b1")
    (d / "refs" / "main").write_text(commit)
    if incomplete:
        (d / "blobs" / "b2.incomplete").write_bytes(b"y" * 10)
    return d


def load_script():
    import importlib
    sys.path.insert(0, str(ROOT / "scripts"))
    return importlib.import_module("compare_base_models")


def test_footprint_guard_uses_budget_not_df(tmp_path, monkeypatch):
    import pytest
    m = load_script()
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf")); monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home/openit-gpu").mkdir(parents=True)
    (tmp_path / "home/openit-gpu/f").write_bytes(b"z" * (1 << 20))
    monkeypatch.setattr(m, "free_gb", lambda *_: 10**6, raising=False)                  # df가 얼마를 보여 주든 쓰지 않는다
    m.footprint_guard({"id": "x/a", "approx_gb": 5}, 20)                                  # 예산 안: 통과
    with pytest.raises(SystemExit, match="사용량 예산 초과"):
        m.footprint_guard({"id": "x/a", "approx_gb": 25}, 20)                             # 후보 예상 크기가 예산을 넘음
    big = 3 * 2**30
    make_cache(tmp_path / "hf/hub", "x/old", 1 << 20)
    (tmp_path / "hf/hub/models--x--old/blobs/b1").write_bytes(b"x" * 1024)             # 작은 캐시
    m.footprint_guard({"id": "x/b", "approx_gb": 19.9}, 20)
    with pytest.raises(SystemExit):
        m.footprint_guard({"id": "x/b", "approx_gb": 20}, 20)                             # 캐시+작업 폴더가 더해져 초과


def test_footprint_guard_counts_cache_and_skips_completed_model(tmp_path, monkeypatch):
    import pytest
    m = load_script()
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf")); monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home/openit-gpu").mkdir(parents=True)
    make_cache(tmp_path / "hf/hub", "x/done", 2 * 10**6)
    assert m.model_cached({"id": "x/done"}) and not m.model_cached({"id": "x/other"})
    m.footprint_guard({"id": "x/done", "approx_gb": 10**6}, 20)                           # 이미 받은 모델은 예상 크기를 더하지 않는다
    make_cache(tmp_path / "hf/hub", "x/part", 10, incomplete=True)
    assert not m.model_cached({"id": "x/part"})
    with pytest.raises(SystemExit):
        m.footprint_guard({"id": "x/part", "approx_gb": 10**6}, 20)                       # 미완성이면 다시 받아야 한다


def test_clean_model_cache_dry_run_then_delete_via_hub_api(tmp_path, capsys):
    pytest = __import__("pytest"); pytest.importorskip("huggingface_hub")
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    c = importlib.import_module("clean_model_cache")
    hub = tmp_path / "hub"
    d = make_cache(hub, "x/gone", 4096); make_cache(hub, "x/keep", 2048)
    assert c.main([], hub) == 0 and c.main(["x/gone"], hub) == 0
    assert d.exists()                                                                       # dry-run은 지우지 않는다
    assert c.main(["x/gone", "--yes"], hub) == 0
    from huggingface_hub import scan_cache_dir
    left = {r.repo_id for r in scan_cache_dir(hub).repos}
    assert left == {"x/keep"} and not list(d.rglob("b1"))                                  # 지정한 모델의 blob만 지운다
    assert c.main(["x/nope"], hub) == 1


def test_candidate_configs_have_size_estimates():
    for f in ("base-model-candidates.yaml", "base-model-candidates-fewshot.yaml"):
        cfg = yaml.safe_load((ROOT / "configs/compare" / f).read_text())
        assert cfg["max_footprint_gb"] == 20 and "min_free_gb" not in cfg and all(c["approx_gb"] > 0 for c in cfg["candidates"])
