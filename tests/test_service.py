"""model-registry 서비스: 토큰 분리, 오류 코드, 상태 코드, tar 안전성, healthz/readyz."""
import io
import json
import tarfile
import threading
import urllib.error
import urllib.request

import pytest
from boundary_contracts import BoundaryError

from model_lab.service.app import Service, make_server, parse_tokens, main
from boundary_contracts.clients import HttpModelRegistry
from tests.test_registry import make


@pytest.fixture
def server(tmp_path):
    svc = Service(tmp_path / "srv", {"rt": "release-gate", "ot": "ops"}, {"wt": "model-lab"}, log=lambda s: None)
    srv = make_server(svc)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def raw(url, method="GET", token=None, body=None):
    r = urllib.request.Request(url, data=body, method=method)
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_health_needs_no_token(server):
    assert raw(server + "/healthz")[0] == 200
    assert raw(server + "/readyz")[0] == 200


def test_tokens_are_separate(server, tmp_path):
    m, ad, rep = make(tmp_path)
    assert code_of(HttpModelRegistry(server, "rt").publish, m, ad, rep) == "PERMISSION_DENIED"        # 읽기 토큰은 쓰기 불가
    assert code_of(HttpModelRegistry(server, "bad").list_ids) == "PERMISSION_DENIED"
    assert code_of(HttpModelRegistry(server, "").list_ids) == "PERMISSION_DENIED"
    w = HttpModelRegistry(server, "wt")
    assert w.publish(m, ad, rep) == m
    assert HttpModelRegistry(server, "rt").get_manifest("pkg-a", "v1") == m           # 읽기 토큰은 읽기 가능
    assert HttpModelRegistry(server, "ot").list_versions("pkg-a") == ["v1"]


def code_of(fn, *a, **k):
    with pytest.raises(BoundaryError) as e:
        fn(*a, **k)
    return e.value.code


def test_status_codes_201_200_409(server, tmp_path):
    m, ad, rep = make(tmp_path)
    w = HttpModelRegistry(server, "wt")
    # 직접 POST해서 상태 코드를 본다
    import tempfile
    from pathlib import Path
    from model_lab.service import tarutil
    def post(manifest, adapter, report):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "m").write_text(json.dumps(manifest)); (td / "r").write_bytes(report)
            f = {"manifest.json": td / "m", "eval_report": td / "r", **tarutil.tree_files(adapter, "adapter/")}
            tarutil.pack(f, td / "b.tar")
            return raw(server + "/v1/packages", "POST", "wt", (td / "b.tar").read_bytes())
    assert post(m, ad, rep)[0] == 201
    assert post(m, ad, rep)[0] == 200
    m2, ad2, rep2 = make(tmp_path, payload=b"other")
    s, body = post(m2, ad2, rep2)
    assert s == 409 and json.loads(body)["code"] == "CONFLICT"


def test_error_status_codes(server):
    assert raw(server + "/v1/packages/x/versions/v1", token="rt")[0] == 404
    assert raw(server + "/v1/eval-reports/nope", token="rt")[0] == 404
    assert raw(server + "/v1/unknown", token="rt")[0] == 404
    assert raw(server + "/v1/packages?limit=0", token="rt")[0] == 400
    assert raw(server + "/v1/packages", token=None)[0] == 403
    s, body = raw(server + "/v1/packages", "POST", "wt", b"not a tar")
    assert s == 400 and json.loads(body)["code"] == "INVALID_RECORD"


def test_unsafe_tar_rejected(server):
    for name in ("../evil", "/abs"):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            ti = tarfile.TarInfo(name); ti.size = 1
            tf.addfile(ti, io.BytesIO(b"x"))
        assert raw(server + "/v1/packages", "POST", "wt", buf.getvalue())[0] == 400
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        ti = tarfile.TarInfo("adapter/link"); ti.type = tarfile.SYMTYPE; ti.linkname = "/etc/passwd"
        tf.addfile(ti)
    assert raw(server + "/v1/packages", "POST", "wt", buf.getvalue())[0] == 400


def test_parse_tokens_and_startup_validation(tmp_path):
    assert parse_tokens("a=t1, b=t2") == {"t1": "a", "t2": "b"}
    with pytest.raises(ValueError):
        parse_tokens("novalue")
    with pytest.raises(ValueError):
        Service(tmp_path / "s", {}, {"x": "ops"})                 # 쓰기 역할은 model-lab만
    assert main({"MODEL_REGISTRY_ROOT": str(tmp_path / "s2")}) == 2        # 토큰 없음 -> 시작 실패


def test_readyz_fails_when_root_unusable(tmp_path):
    svc = Service(tmp_path / "srv", {"r": "x"}, {}, log=lambda s: None)
    import shutil
    shutil.rmtree(tmp_path / "srv" / ".tmp")
    assert svc.ready() is False


def test_i3_server_conformance_with_real_client(server, tmp_path):
    """통합 시험(서버 적합성): 실제 HttpModelRegistry로 게시(tar) -> 조회 -> 어댑터 내려받기 -> 해시 검증."""
    from boundary_contracts import canonical
    w, r = HttpModelRegistry(server, "wt"), HttpModelRegistry(server, "rt")
    m, ad, rep = make(tmp_path)
    assert w.publish(m, ad, rep) == m
    assert w.publish(m, ad, rep) == m                                    # 같은 ID·버전·내용
    assert r.get_manifest("pkg-a", "v1") == m and r.list_ids() == (["pkg-a"], None) and r.list_versions("pkg-a") == ["v1"]
    out = r.open_adapter("pkg-a", "v1", dest=tmp_path / "dl")
    assert canonical.directory_hash(out) == m["adapter_hash"]
    assert r.get_eval_report("eval-1") == rep
    m2, ad2, rep2 = make(tmp_path, payload=b"other")
    assert code_of(w.publish, m2, ad2, rep2) == "CONFLICT"                # 같은 ID·버전, 다른 내용
    m3, ad3, rep3 = make(tmp_path, pid="pkg-z", ref="eval-z", report=b"z")
    assert code_of(r.publish, m3, ad3, rep3) == "PERMISSION_DENIED"       # 읽기 토큰의 게시
    assert code_of(r.get_manifest, "nope", "v1") == "NOT_FOUND"


def test_rejected_upload_still_returns_real_error_not_connection_reset(server):
    """본문을 읽기 전에 403/400으로 답하면 아직 보내는 중인 클라이언트가 연결 초기화(UNAVAILABLE)를 본다.
    서버가 본문을 비우고 답하는지 큰 본문으로 반복 확인한다(간헐 실패의 회귀 시험)."""
    big = b"x" * (40 * 1024 * 1024)
    for _ in range(8):
        assert raw(server + "/v1/packages", "POST", "rt", big)[0] == 403          # 읽기 토큰
        assert raw(server + "/v1/packages", "POST", "wt", big)[0] == 400          # tar가 아님
