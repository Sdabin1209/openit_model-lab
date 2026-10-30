"""HTTP 서버 교체(D111, ADR 0023)가 지켜야 할 성질. 구현과 무관하게 소켓 수준에서 본다.
수정 전(표준 라이브러리 서버)에서 먼저 통과하는 것을 확인한 뒤 waitress로 바꿨다."""
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from model_lab.components import link_registry_serve as tarutil
from model_lab.service.app import Service
from tests.conftest import ROOT
from tests.doubles.serve import serve, stop
from tests.test_registry import make

RT, WT = "9f2c4e1ab7d03856" * 2, "5d8e1c7a0b3f2946" * 2


def start(tmp_path, **kw):
    svc = Service(tmp_path / "srv", {RT: "release-gate"}, {WT: "model-lab"}, log=lambda s: None, **kw)
    srv, port = serve(svc)
    return svc, srv, port


def get(port, path="/healthz", token=None, timeout=5):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.status


def package_tar(tmp_path) -> bytes:
    m, ad, rep = make(tmp_path)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "m").write_text(json.dumps(m)); (td / "r").write_bytes(rep)
        tarutil.pack({"manifest.json": td / "m", "eval_report": td / "r", **tarutil.tree_files(ad, "adapter/")}, td / "b.tar")
        return (td / "b.tar").read_bytes()


def test_clients_that_stall_mid_request_do_not_block_others(tmp_path):
    svc, srv, port = start(tmp_path)
    held = []
    try:
        for part in (b"GET /healthz HTTP/1.1\r\nHos", b"POST /v1/packages HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\nabc", b"G"):
            s = socket.create_connection(("127.0.0.1", port)); s.sendall(part); held.append(s)
        time.sleep(0.2)
        assert get(port) == 200 and get(port, "/readyz") == 200 and get(port, "/v1/packages", RT) == 200
    finally:
        for s in held:
            s.close()
        stop(srv)


def test_stalled_connection_is_closed_after_the_timeout(tmp_path):
    svc, srv, port = start(tmp_path, socket_timeout=1.0)
    try:
        s = socket.create_connection(("127.0.0.1", port)); s.sendall(b"POST /v1/packages HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\nabc")
        s.settimeout(8)
        t0 = time.time()
        assert s.recv(10) in (b"", ) or True       # 오류 응답을 먼저 줄 수도 있다
        while s.recv(4096):
            pass                                    # 끝내 서버가 연결을 닫아야 한다
        assert time.time() - t0 < 7
        s.close()
    finally:
        stop(srv)


def test_a_slow_request_does_not_block_a_concurrent_one(tmp_path):
    svc, srv, port = start(tmp_path)
    started = threading.Event()
    real = svc.registry.list_ids

    def slow(**kw):
        started.set(); time.sleep(1.5)
        return real(**kw)
    svc.registry.list_ids = slow
    try:
        t = threading.Thread(target=lambda: get(port, "/v1/packages", RT, 10)); t.start()
        assert started.wait(5)
        t0 = time.time()
        assert get(port) == 200 and time.time() - t0 < 1.0
        t.join(10)
    finally:
        stop(srv)


def test_declared_body_over_the_limit_is_refused_without_receiving_it(tmp_path):
    svc, srv, port = start(tmp_path, max_body=1 << 20)
    try:
        s = socket.create_connection(("127.0.0.1", port))
        s.sendall(b"POST /v1/packages HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + WT.encode() + b"\r\nContent-Length: " + str(8 << 20).encode() + b"\r\n\r\n")
        s.settimeout(5)
        line = s.recv(4096).split(b"\r\n")[0]            # 본문을 한 바이트도 보내지 않았는데 답한다
        assert b" 400 " in line or b" 413 " in line
        s.close()
    finally:
        stop(srv)


def _env(tmp_path, **extra):
    env_ = {**os.environ, "MODEL_REGISTRY_ROOT": str(tmp_path / "data"), "MODEL_REGISTRY_PORT": "0", "MODEL_REGISTRY_HOST": "127.0.0.1",
            "MODEL_REGISTRY_READ_TOKENS": "rg=" + RT, "MODEL_REGISTRY_WRITE_TOKENS": "model-lab=" + WT, "PYTHONPATH": str(ROOT), **extra}
    for k in ("MODEL_REGISTRY_READ_TOKENS_FILE", "MODEL_REGISTRY_WRITE_TOKENS_FILE"):
        env_.pop(k, None)
    return env_


def test_sigterm_lets_an_upload_in_progress_finish(tmp_path):
    """본문을 보내는 중에 종료 신호를 받아도 그 요청은 끝까지 받아 게시하고 응답한 뒤 종료한다(유예 안에서)."""
    body = package_tar(tmp_path)
    p = subprocess.Popen([sys.executable, "-m", "model_lab.service"], env=_env(tmp_path), cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        port = int(p.stdout.readline().split()[-1])
        s = socket.create_connection(("127.0.0.1", port)); s.settimeout(10)
        half = len(body) // 2
        s.sendall(b"POST /v1/packages HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + WT.encode() + b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body[:half])
        time.sleep(0.5)
        p.send_signal(signal.SIGTERM)
        time.sleep(0.5)
        s.sendall(body[half:])
        got = b""
        while b"}" not in got:
            chunk = s.recv(65536)
            if not chunk:
                break
            got += chunk
        assert got.split(b"\r\n")[0].split()[1] == b"201", got[:200]
        assert p.wait(timeout=15) == 0
        s.close()
    finally:
        if p.poll() is None:
            p.kill()


def test_client_reads_over_the_limit_413_as_invalid_record_not_retryable(tmp_path):
    """waitress가 앱 전에 내는 413은 영구 오류다. 계약 클라이언트(v2.2.2, CONVENTIONS 9절)가 INVALID_RECORD로 읽어야 한다.
    U29(OPEN_GAPS): 서버가 413을 내고 읽지 않은 본문이 남은 채 연결을 닫으면 TCP RST가 나가, 클라이언트가 413을 읽기 전에 연결 끊김을
    받을 수 있다(약 10%, 100회 중 12회 안팎으로 관찰). 그때 계약 클라이언트는 UNAVAILABLE로 읽는다. 계약의 다음 릴리스가 쓰기 실패 뒤에도
    응답을 읽게 하면 이 허용은 지운다. 그때까지 허용하는 것은 '연결 수준 끊김이 원인인 UNAVAILABLE'뿐이고, 어느 쪽이든 아무것도 게시되지 않아야 한다."""
    from boundary_contracts import BoundaryError
    from boundary_contracts.clients import HttpModelRegistry
    svc, srv, port = start(tmp_path, max_body=1000)
    try:
        m, ad, rep = make(tmp_path, payload=b"w" * 5000)
        with pytest.raises(BoundaryError) as e:
            HttpModelRegistry(f"http://127.0.0.1:{port}", WT).publish(m, ad, rep)
        if e.value.code != "INVALID_RECORD":
            cause = e.value.__cause__ or e.value.__context__
            reason = getattr(cause, "reason", cause)
            assert e.value.code == "UNAVAILABLE" and isinstance(reason, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)), \
                f"{e.value.code}: 413도 연결 끊김도 아니다: {e.value!r} / {reason!r}"
        assert svc.registry.list_ids()[0] == []             # 어느 쪽이든 서버에는 아무것도 게시되지 않았다
    finally:
        stop(srv)
