"""T24: 기반 모델의 실제 커밋과 캐시 유무를 huggingface_hub 공개 API가 알려 주는 대로 읽는다.
기대값은 HF 캐시 문서의 배치(refs/<revision> 파일 = 커밋 해시, snapshots/<커밋>/ 에 파일)와 snapshot_download의 반환 경로에서 온다.
transformers 비공개 속성(config._commit_hash)은 근거로 쓰지 않는다."""
import types

import pytest

pytest.importorskip("huggingface_hub")

from model_lab.wiring import train_flow as tw
from model_lab.components.lora_train_hf import resolved_revision as _rr
from model_lab.components.hf_cache_lookup import cached_commit


def resolved_revision(model, base):
    return _rr(model, base, cached_commit)

COMMIT = "a" * 40


def make_cache(hub, repo="org/m", rev="main", commit=COMMIT, files=True):
    root = hub / ("models--" + repo.replace("/", "--"))
    (root / "refs").mkdir(parents=True)
    (root / "refs" / rev).write_text(commit)
    snap = root / "snapshots" / commit
    snap.mkdir(parents=True)
    if files:
        (snap / "config.json").write_text("{}")
    return root


@pytest.fixture
def hub(tmp_path, monkeypatch):
    h = tmp_path / "hub"
    h.mkdir()
    # huggingface_hub는 import 때 상수를 읽으므로 공개 상수를 직접 바꾼다
    import huggingface_hub.constants as c
    monkeypatch.setattr(c, "HF_HUB_CACHE", str(h))
    monkeypatch.setenv("HF_HUB_CACHE", str(h))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    return h


def test_commit_comes_from_public_api_not_private_attribute(hub):
    make_cache(hub)
    model = types.SimpleNamespace(config=types.SimpleNamespace(_commit_hash="b" * 40))   # 비공개 속성이 틀린 값을 가져도
    assert resolved_revision(model, {"id": "org/m", "revision": "main"}) == COMMIT


def test_commit_found_under_hf_hub_cache_env(hub):
    make_cache(hub)
    assert resolved_revision(types.SimpleNamespace(), {"id": "org/m", "revision": "main"}) == COMMIT


def test_doctor_reports_cache_missing_when_no_snapshot_files(hub, tmp_path):
    make_cache(hub, files=False)                        # 디렉터리만 있고 받은 파일이 없다
    row = [r for r in tw.doctor(_cfg(tmp_path, "org/m")) if r[1] == "기반 모델 캐시"][0]
    assert row[0] == "WARN"


def test_doctor_reports_cache_ok_when_snapshot_present(hub, tmp_path):
    make_cache(hub)
    row = [r for r in tw.doctor(_cfg(tmp_path, "org/m")) if r[1] == "기반 모델 캐시"][0]
    assert row[0] == "OK"


def _cfg(tmp_path, model_id):
    import yaml
    from tests.conftest import ROOT
    raw = yaml.safe_load((ROOT / "configs" / "train.yaml").read_text())
    raw["base_model"] = {"id": model_id, "revision": "main"}
    raw["server"].update(python_modules=["json"], require_gpu=False)
    p = tmp_path / "t.yaml"
    p.write_text(yaml.safe_dump(raw))
    return tw.load_train_config(p)
