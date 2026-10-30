"""감사 지적(ML-1~ML-6, ML-12)에 대한 시험: 게시 중단 재시도, 시작 정리, 자원 한도, 디스크 가드, 게시자 기록, 학습 전 점검, 계보."""
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml
from boundary_contracts import BoundaryError

from model_lab.wiring import train_flow as tw
from model_lab.wiring.attach_flow import package_adapters
from model_lab.wiring.errors import PipelineError
from model_lab.components.model_registry import FileModelRegistry
from model_lab.components import link_registry_serve as tarutil
from model_lab.service.app import Service, drain
from tests.doubles.serve import serve, stop
from tests.conftest import ROOT
from tests.test_attach import DS, setup  # noqa: F401  (fixture)
from tests.test_registry import make
from tests.test_train_wrap import env  # noqa: F401  (fixture)


# ---------- ML-3 게시 중단 뒤 재시도 ----------
def test_retry_after_interrupted_publish_has_no_duplicate_version(tmp_path):
    reg = FileModelRegistry(tmp_path / "r")
    m, ad, rep = make(tmp_path)
    # 중단 재현: order와 adapters는 쓰였고 매니페스트는 없다
    (tmp_path / "r/manifests/pkg-a").mkdir(parents=True)
    (tmp_path / "r/manifests/pkg-a/_order.json").write_text('["v1"]')
    (tmp_path / "r/adapters/pkg-a/v1").mkdir(parents=True)
    (tmp_path / "r/adapters/pkg-a/v1/junk").write_text("x")
    assert reg.list_versions("pkg-a") == []
    reg.publish(m, ad, rep)
    assert reg.list_versions("pkg-a") == ["v1"]
    reg.publish(m, ad, rep)                                    # 멱등
    assert reg.list_versions("pkg-a") == ["v1"]


def test_legacy_duplicate_order_is_deduplicated_on_read(tmp_path):
    reg = FileModelRegistry(tmp_path / "r")
    m, ad, rep = make(tmp_path)
    reg.publish(m, ad, rep)
    (tmp_path / "r/manifests/pkg-a/_order.json").write_text('["v1","v1"]')   # 이전 버전이 남긴 상태
    assert reg.list_versions("pkg-a") == ["v1"]


def test_recover_removes_interrupted_leftovers_and_service_start_cleans_tmp(tmp_path):
    root = tmp_path / "srv"
    Service(root, {"r": "x"}, {"w": "model-lab"}, log=lambda s: None)
    (root / ".tmp/req-123").mkdir()
    (root / ".tmp/req-123/body.tar").write_bytes(b"x")
    (root / "adapters/pkg-a").mkdir(parents=True)
    (root / "adapters/pkg-a/.tmp-v1").mkdir()
    (root / "manifests/pkg-a").mkdir(parents=True)
    (root / "manifests/pkg-a/.tmp-abc").write_text("x")
    logs = []
    Service(root, {"r": "x"}, {"w": "model-lab"}, log=logs.append)                # 재시작
    assert not (root / ".tmp/req-123").exists() and not (root / "adapters/pkg-a/.tmp-v1").exists()
    assert not (root / "manifests/pkg-a/.tmp-abc").exists() and any("2개" in l for l in logs)


# ---------- 서비스 도우미 ----------
def start(tmp_path, **kw):
    svc = Service(tmp_path / "srv", {"rt": "release-gate"}, {"wt": "model-lab"}, log=lambda s: None, **kw)
    srv, port = serve(svc)
    return svc, srv, f"http://127.0.0.1:{port}"


def post_pkg(url, tmp_path, token="wt", payload=b"w"):
    m, ad, rep = make(tmp_path, payload=payload)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "m").write_text(json.dumps(m)); (td / "r").write_bytes(rep)
        tarutil.pack({"manifest.json": td / "m", "eval_report": td / "r", **tarutil.tree_files(ad, "adapter/")}, td / "b.tar")
        r = urllib.request.Request(url + "/v1/packages", data=(td / "b.tar").read_bytes(), method="POST")
        r.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


# ---------- ML-6 게시자 기록 ----------
def test_publisher_is_recorded_beside_manifest_and_not_overwritten(tmp_path):
    svc, srv, url = start(tmp_path)
    try:
        assert post_pkg(url, tmp_path)[0] == 201
        rec = svc.registry.get_publisher("pkg-a", "v1")
        assert rec["role"] == "model-lab" and rec["remote"] == "127.0.0.1" and rec["content_hash"].startswith("sha256:")
        assert "wt" not in json.dumps(rec)                                          # 토큰 값은 기록하지 않는다
        p = tmp_path / "srv/manifests/pkg-a/v1.published.json"
        p.write_text(json.dumps({**rec, "published_at": "첫 게시"}))
        assert post_pkg(url, tmp_path)[0] == 200                                    # 멱등 재게시
        assert json.loads(p.read_text())["published_at"] == "첫 게시"
    finally:
        stop(srv)


# ---------- ML-2 디스크 ----------
def test_publish_refused_when_free_space_is_short_and_leaves_no_tmp(tmp_path):
    free = {"v": 0}
    svc, srv, url = start(tmp_path, free_bytes=lambda: free["v"])
    try:
        s, body = post_pkg(url, tmp_path)
        assert s == 503 and body["code"] == "UNAVAILABLE"
        assert svc.registry.list_ids() == ([], None) and not any(svc.tmp.iterdir())
        free["v"] = 10 << 30
        assert post_pkg(url, tmp_path)[0] == 201                                    # 여유가 생기면 같은 요청이 된다
    finally:
        stop(srv)


def test_link_on_commit_saves_a_copy_only_when_asked(tmp_path):
    m, ad, rep = make(tmp_path)
    a = FileModelRegistry(tmp_path / "plain"); a.publish(m, ad, rep)
    assert os.stat(ad / "adapter_model.safetensors").st_nlink == 1                          # 호출자의 원본과 저장본을 공유하지 않는다
    b = FileModelRegistry(tmp_path / "linked", link_on_commit=True); b.publish(m, ad, rep)
    assert os.stat(ad / "adapter_model.safetensors").st_nlink == 2
    out = b.open_adapter("pkg-a", "v1", dest=tmp_path / "o")                       # 해시 검증을 통과한다
    assert (out / "adapter_model.safetensors").read_bytes() == b"w"


# ---------- ML-1 자원 한도 ----------
def test_second_concurrent_publish_gets_503(tmp_path):
    svc, srv, url = start(tmp_path)
    try:
        assert svc.publish_slots.acquire(blocking=False)                            # 게시 하나가 진행 중인 상태
        s, body = post_pkg(url, tmp_path)
        assert s == 503 and body["code"] == "UNAVAILABLE"
        svc.publish_slots.release()
        assert post_pkg(url, tmp_path)[0] == 201
    finally:
        stop(srv)


# ---------- ML-2/ML-12 학습 전 점검 ----------
def test_low_disk_blocks_training_by_default_and_policy_can_relax(env, monkeypatch):  # noqa: F811
    raw, write, tmp = env
    monkeypatch.setattr(__import__("model_lab.components.server_train_wrap", fromlist=["x"]), "disk_free_gb", lambda cfg: (5.0, tmp))
    raw["output"]["min_free_gb"] = 30
    cfg = tw.load_train_config(write())
    with pytest.raises(PipelineError, match="디스크 여유가 부족"):
        tw.check_ready(cfg)
    assert [r for r in tw.doctor(cfg) if r[1] == "디스크"][0][0] == "FAIL"
    raw["output"]["disk_policy"] = "warn"
    cfg = tw.load_train_config(write())
    tw.check_ready(cfg)                                                            # 막지 않는다
    assert [r for r in tw.doctor(cfg) if r[1] == "디스크"][0][0] == "WARN"
    raw["output"]["disk_policy"] = "off"
    assert not [r for r in tw.doctor(tw.load_train_config(write())) if r[1] == "디스크"]


def test_server_script_hash_comparison(env):  # noqa: F811
    raw, write, tmp = env
    sp = tw.script_path(tw.load_train_config(write()))
    good = hashlib.sha256(sp.read_bytes()).hexdigest()
    row = lambda c: [r for r in tw.doctor(c) if r[1] == "서버 코드 해시"][0]
    assert row(tw.load_train_config(write()))[0] == "WARN"                         # 기록 없음: 현재 해시를 알려 준다
    assert good in row(tw.load_train_config(write()))[2]
    raw["server"]["script_sha256"] = {"LORA_A": good}
    cfg = tw.load_train_config(write())
    assert row(cfg)[0] == "OK"
    tw.check_ready(cfg)
    raw["server"]["script_sha256"] = {"LORA_A": "0" * 64}                          # 서버 코드가 바뀐 것처럼
    cfg = tw.load_train_config(write())
    assert row(cfg)[0] == "FAIL"
    with pytest.raises(PipelineError, match="서버 코드가 바뀌었다"):
        tw.check_ready(cfg)
    raw["server"]["script_sha256"] = {"LORA_A": "xyz"}
    with pytest.raises(Exception, match="형식 위반"):
        tw.load_train_config(write())


# ---------- ML-5 계보 ----------
def _merge(base, over):
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def _record(kw, rec_role, **over):
    """계약상 유효한 학습 기록(스키마 TrainWrapRecord 전체). 변형은 깊은 병합으로 한 곳만 바꾼다."""
    from model_lab.components.train_record_verify import _sha256
    role = rec_role
    ad = Path(kw["adapters"][role])
    rec = {"record_version": 1, "role": role, "config_hash": "sha256:" + "c" * 64, "command": ["python3", "train.py"], "exit_code": 0,
           "server_script": "/srv/train.py", "server_script_sha256": "d" * 64,
           "base_model": {"id": "Qwen/Qwen2.5-1.5B-Instruct", "requested_revision": "main", "base_model_revision": None},
           "dataset": {**{k: v for k, v in DS.items()},
                       "file_sha256": {f"x_{sp}.jsonl": "e" * 64 for sp in ("train", "val", "test")}},
           "adapter": {"dir": str(ad), "r": 16, "files": {f: _sha256(ad / f) for f in ("adapter_model.safetensors", "adapter_config.json")}},
           "hyperparams": {"epochs": 3}, "notes": []}
    rec = _merge(rec, over)
    p = ad.parent / f"{role}.train-wrap.json"
    p.write_text(json.dumps(rec))
    return p


def test_train_record_goes_into_package_lineage_and_missing_record_warns(setup):  # noqa: F811
    kw, tmp = setup
    res = package_adapters(**kw, publish=False)
    assert all("학습 기록이 없어" in res[r]["warnings"][0] for r in res)
    recs = {r: _record(kw, r) for r in ("LORA_A", "LORA_B")}
    res = package_adapters(**kw, publish=False, train_records=recs)
    assert not any("warnings" in v for v in res.values())
    m = json.loads((kw["out"] / "package/LORA_A.manifest.json").read_text())
    lin = m["ext"]["model-lab"]["lineage"]
    want = "sha256:" + hashlib.sha256(recs["LORA_A"].read_bytes()).hexdigest()
    assert lin["train_record_sha256"] == want and lin["server_script_sha256"] == "d" * 64 and lin["hyperparams"] == {"epochs": 3}


@pytest.mark.parametrize("mut,msg", [
    ({"role": "LORA_B"}, "역할"),
    ({"dataset": {"id": "other"}}, "dataset.id"),
    ({"dataset": {"content_hash": "sha256:" + "9" * 64}}, "content_hash"),
    ({"base_model": {"id": "x/y"}}, "기반 모델"),
    ({"adapter": {"files": {"adapter_model.safetensors": "0" * 64}}}, "해시가 지금 어댑터"),
    ({"record_version": 2}, "형식이 아니다"),
])
def test_train_record_that_does_not_match_is_rejected(setup, mut, msg):  # noqa: F811
    kw, tmp = setup
    recs = {"LORA_A": _record(kw, "LORA_A", **mut), "LORA_B": _record(kw, "LORA_B")}
    with pytest.raises(PipelineError, match=msg):
        package_adapters(**kw, publish=False, train_records=recs)


# ---------- 3차 점검: 토큰 강도 ----------
def test_start_refuses_short_or_monotone_tokens_without_echoing_them(tmp_path, capsys):
    from model_lab.service.app import main
    root = str(tmp_path / "r")
    for bad in ("short", "x" * 40, "ab" * 3):
        assert main({"MODEL_REGISTRY_WRITE_TOKENS": f"model-lab={bad}", "MODEL_REGISTRY_ROOT": root}) == 2
        err = capsys.readouterr().err
        assert "토큰" in err and bad not in err
    # 읽기 토큰도 같은 요건이다
    assert main({"MODEL_REGISTRY_READ_TOKENS": "rg=short", "MODEL_REGISTRY_WRITE_TOKENS": "model-lab=" + "5d8e1c7a0b3f2946" * 2, "MODEL_REGISTRY_ROOT": root}) == 2


def test_start_accepts_strong_tokens_and_dev_can_lower_the_minimum(tmp_path, monkeypatch):
    import model_lab.service.app as app
    monkeypatch.setattr(app, "make_server", lambda svc, host, port, **kw: (_ for _ in ()).throw(KeyboardInterrupt()))
    base = {"MODEL_REGISTRY_ROOT": str(tmp_path / "r")}
    with pytest.raises(KeyboardInterrupt):
        app.main({**base, "MODEL_REGISTRY_WRITE_TOKENS": "model-lab=" + os.urandom(16).hex()})            # 32자 16진수(128비트)
    assert app.main({**base, "MODEL_REGISTRY_WRITE_TOKENS": "model-lab=devtoken12"}) == 2
    with pytest.raises(KeyboardInterrupt):
        app.main({**base, "MODEL_REGISTRY_WRITE_TOKENS": "model-lab=devtoken12", "MODEL_REGISTRY_MIN_TOKEN_LEN": "8"})

