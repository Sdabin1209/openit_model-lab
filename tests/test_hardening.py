"""감사 지적(ML-1~ML-6, ML-12)에 대한 시험: 게시 중단 재시도, 시작 정리, 자원 한도, 디스크 가드, 게시자 기록, 학습 전 점검, 계보."""
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml
from boundary_contracts import BoundaryError

from model_lab import train_wrap as tw
from model_lab.attach import package_adapters
from model_lab.errors import PipelineError
from model_lab.registry import FileModelRegistry
from model_lab.service import tarutil
from model_lab.service.app import Service, make_server
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
    srv = make_server(svc)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return svc, srv, f"http://127.0.0.1:{srv.server_address[1]}"


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
        srv.shutdown()


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
        srv.shutdown()


def test_link_on_commit_saves_a_copy_only_when_asked(tmp_path):
    m, ad, rep = make(tmp_path)
    a = FileModelRegistry(tmp_path / "plain"); a.publish(m, ad, rep)
    assert os.stat(ad / "adapter_model.bin").st_nlink == 1                          # 호출자의 원본과 저장본을 공유하지 않는다
    b = FileModelRegistry(tmp_path / "linked", link_on_commit=True); b.publish(m, ad, rep)
    assert os.stat(ad / "adapter_model.bin").st_nlink == 2
    out = b.open_adapter("pkg-a", "v1", dest=tmp_path / "o")                       # 해시 검증을 통과한다
    assert (out / "adapter_model.bin").read_bytes() == b"w"


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
        srv.shutdown()


def test_connection_cap_answers_503_and_idle_connection_times_out(tmp_path):
    svc, srv, url = start(tmp_path, max_connections=1, socket_timeout=1.0)
    host, port = "127.0.0.1", srv.server_address[1]
    try:
        hold = socket.create_connection((host, port))                              # 요청을 보내지 않고 자리를 잡는다
        time.sleep(0.2)
        over = socket.create_connection((host, port)); over.settimeout(3)
        assert b"503" in over.recv(4096).split(b"\r\n")[0]
        over.close()
        hold.settimeout(5)
        t0 = time.time()
        assert hold.recv(10) == b"" and time.time() - t0 < 4                        # 소켓 timeout 뒤 서버가 연결을 닫는다
        hold.close()
        time.sleep(0.2)
        with urllib.request.urlopen(url + "/healthz") as r:                         # 자리가 풀리면 다시 받는다
            assert r.status == 200
    finally:
        srv.shutdown()


def test_repeated_bad_tokens_are_throttled_but_valid_tokens_always_pass(tmp_path):
    svc, srv, url = start(tmp_path, auth_fail_limit=3)
    try:
        def get(tok):
            r = urllib.request.Request(url + "/v1/packages"); r.add_header("Authorization", f"Bearer {tok}")
            try:
                return urllib.request.urlopen(r).status
            except urllib.error.HTTPError as e:
                return e.code
        assert [get("bad") for _ in range(3)] == [403, 403, 403]
        assert get("bad") == 503 and svc.auth_blocked("127.0.0.1")                  # 틀린 토큰은 제한된다
        assert get("rt") == 200 and get("wt") == 200                                 # 같은 주소의 정상 토큰은 영향이 없다(프록시 뒤 호출자)
        assert post_pkg(url, tmp_path)[0] == 201                                     # 쓰기도 마찬가지
        # 유효한 읽기 토큰으로 쓰기를 시도한 403(역할 부족)은 실패 횟수에 넣지 않는다
        before = len(svc._fails.get("127.0.0.1", []))
        assert post_pkg(url, tmp_path, token="rt", payload=b"z")[0] == 403
        assert len(svc._fails.get("127.0.0.1", [])) == before
        svc.auth_fail_window = 0.0                                                   # 창이 지나면 틀린 토큰도 다시 403
        time.sleep(0.01)
        assert get("bad") == 403
    finally:
        srv.shutdown()


# ---------- ML-3 SIGTERM ----------
def test_sigterm_stops_cleanly_and_removes_tmp(tmp_path):
    env_ = {**os.environ, "MODEL_REGISTRY_ROOT": str(tmp_path / "data"), "MODEL_REGISTRY_PORT": "0", "MODEL_REGISTRY_HOST": "127.0.0.1",
            "MODEL_REGISTRY_READ_TOKENS": "rg=" + "9f2c4e1ab7d03856" * 2, "MODEL_REGISTRY_WRITE_TOKENS": "model-lab=" + "5d8e1c7a0b3f2946" * 2, "PYTHONPATH": str(ROOT)}
    for k in ("MODEL_REGISTRY_READ_TOKENS_FILE", "MODEL_REGISTRY_WRITE_TOKENS_FILE"):
        env_.pop(k, None)
    p = subprocess.Popen([sys.executable, "-m", "model_lab.service"], env=env_, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        assert "시작" in p.stdout.readline()
        assert (tmp_path / "data/.tmp").is_dir()
        p.send_signal(signal.SIGTERM)
        rc = p.wait(timeout=15); assert rc == 0, p.stdout.read()
        assert not (tmp_path / "data/.tmp").exists()
    finally:
        if p.poll() is None:
            p.kill()


# ---------- ML-2/ML-12 학습 전 점검 ----------
def test_low_disk_blocks_training_by_default_and_policy_can_relax(env, monkeypatch):  # noqa: F811
    raw, write, tmp = env
    monkeypatch.setattr(tw, "disk_free_gb", lambda cfg: (5.0, tmp))
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
def _record(kw, rec_role, **over):
    from model_lab.train_wrap import _sha256
    role = rec_role
    ad = Path(kw["adapters"][role])
    rec = {"record_version": 1, "role": role, "config_hash": "sha256:" + "c" * 64, "server_script_sha256": "d" * 64,
           "base_model": {"id": "Qwen/Qwen2.5-1.5B-Instruct", "requested_revision": "main", "base_model_revision": None},
           "dataset": {**{k: v for k, v in DS.items()}, "file_sha256": {"x_train.jsonl": "e" * 64}},
           "adapter": {"files": {f: _sha256(ad / f) for f in ("adapter_model.safetensors", "adapter_config.json")}},
           "hyperparams": {"epochs": 3}}
    rec.update(over)
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
    ({"dataset": {"id": "other", "version": "v1"}}, "dataset.id"),
    ({"dataset": {"id": DS["id"], "version": "v1", "content_hash": "sha256:" + "9" * 64}}, "content_hash"),
    ({"base_model": {"id": "x/y"}}, "기반 모델"),
    ({"adapter": {"files": {"adapter_model.safetensors": "0" * 64}}}, "해시가 지금 어댑터"),
])
def test_train_record_that_does_not_match_is_rejected(setup, mut, msg):  # noqa: F811
    kw, tmp = setup
    recs = {"LORA_A": _record(kw, "LORA_A", **mut), "LORA_B": _record(kw, "LORA_B")}
    with pytest.raises(PipelineError, match=msg):
        package_adapters(**kw, publish=False, train_records=recs)


# ---------- ML-2 재점검: 유휴 keep-alive 연결이 있어도 빨리 끝난다 ----------
def test_sigterm_with_idle_keepalive_connection_exits_fast(tmp_path):
    env_ = {**os.environ, "MODEL_REGISTRY_ROOT": str(tmp_path / "data"), "MODEL_REGISTRY_PORT": "0", "MODEL_REGISTRY_HOST": "127.0.0.1",
            "MODEL_REGISTRY_READ_TOKENS": "rg=" + "9f2c4e1ab7d03856" * 2, "MODEL_REGISTRY_WRITE_TOKENS": "model-lab=" + "5d8e1c7a0b3f2946" * 2, "PYTHONPATH": str(ROOT),
            "MODEL_REGISTRY_SOCKET_TIMEOUT": "30"}
    for k in ("MODEL_REGISTRY_READ_TOKENS_FILE", "MODEL_REGISTRY_WRITE_TOKENS_FILE"):
        env_.pop(k, None)
    p = subprocess.Popen([sys.executable, "-m", "model_lab.service"], env=env_, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        port = int(p.stdout.readline().split()[-1])
        s = socket.create_connection(("127.0.0.1", port))
        s.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n")
        got = b""
        while b"}" not in got:
            got += s.recv(4096)
        assert b"200" in got                                 # 응답을 모두 받았고 연결은 keep-alive로 열려 있다(유휴)
        t0 = time.time()
        p.send_signal(signal.SIGTERM)
        assert p.wait(timeout=10) == 0
        assert time.time() - t0 < 5                          # 소켓 timeout(30초)을 기다리지 않는다
        s.settimeout(2)
        assert s.recv(10) == b""                             # 유휴 연결은 서버가 닫았다
        s.close()
    finally:
        if p.poll() is None:
            p.kill()


def test_drain_lets_in_flight_request_finish_with_connection_close(tmp_path):
    svc, srv, url = start(tmp_path)
    started = threading.Event()
    real = svc.registry.list_ids

    def slow(**kw):
        started.set(); time.sleep(1.0)
        return real(**kw)
    svc.registry.list_ids = slow
    out = {}

    def call():
        r = urllib.request.Request(url + "/v1/packages"); r.add_header("Authorization", "Bearer rt")
        with urllib.request.urlopen(r) as resp:
            out["status"], out["conn"] = resp.status, resp.headers.get("Connection")
    t = threading.Thread(target=call); t.start()
    assert started.wait(5)
    t0 = time.time()
    srv.drain(5.0)                                           # 진행 중 요청이 끝날 때까지 기다린다
    t.join(5)
    assert out == {"status": 200, "conn": "close"} and 0.5 < time.time() - t0 < 4
    srv.shutdown()


# ---------- 3차 점검: 토큰 강도, 연결 재사용 상태 ----------
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
    monkeypatch.setattr(app, "make_server", lambda svc, host, port: (_ for _ in ()).throw(KeyboardInterrupt()))
    base = {"MODEL_REGISTRY_ROOT": str(tmp_path / "r")}
    with pytest.raises(KeyboardInterrupt):
        app.main({**base, "MODEL_REGISTRY_WRITE_TOKENS": "model-lab=" + os.urandom(16).hex()})            # 32자 16진수(128비트)
    assert app.main({**base, "MODEL_REGISTRY_WRITE_TOKENS": "model-lab=devtoken12"}) == 2
    with pytest.raises(KeyboardInterrupt):
        app.main({**base, "MODEL_REGISTRY_WRITE_TOKENS": "model-lab=devtoken12", "MODEL_REGISTRY_MIN_TOKEN_LEN": "8"})


def test_per_request_state_is_reset_on_a_reused_connection(tmp_path):
    """같은 keep-alive 연결에서 앞 요청이 본문을 읽었어도(_consumed) 다음 거절이 본문을 버리고 실제 오류를 돌려준다."""
    svc, srv, url = start(tmp_path)
    host, port = "127.0.0.1", srv.server_address[1]
    try:
        m, ad, rep = make(tmp_path)
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "m").write_text(json.dumps(m)); (td / "r").write_bytes(rep)
            tarutil.pack({"manifest.json": td / "m", "eval_report": td / "r", **tarutil.tree_files(ad, "adapter/")}, td / "b.tar")
            good = (td / "b.tar").read_bytes()
        def req(tok, body):
            return (b"POST /v1/packages HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + tok + b"\r\nContent-Length: "
                    + str(len(body)).encode() + b"\r\n\r\n" + body)
        def read_resp(s):
            buf = b""
            while b"\r\n\r\n" not in buf:
                buf += s.recv(65536)
            head, _, rest = buf.partition(b"\r\n\r\n")
            n = int([l for l in head.split(b"\r\n") if l.lower().startswith(b"content-length")][0].split(b":")[1])
            while len(rest) < n:
                rest += s.recv(65536)
            return head
        for _ in range(5):
            s = socket.create_connection((host, port)); s.settimeout(5)
            s.sendall(req(b"wt", good))
            assert b" 20" in read_resp(s).split(b"\r\n")[0]                       # 200 또는 201: 이 요청이 본문을 읽었다
            s.sendall(req(b"rt", b"x" * (900 * 1024)))                                # 같은 연결: 읽기 토큰의 쓰기 시도
            assert b" 403 " in read_resp(s).split(b"\r\n")[0]
            s.close()
    finally:
        srv.shutdown()
