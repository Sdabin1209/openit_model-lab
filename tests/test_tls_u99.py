"""U99: 영역 사이 통신은 TLS, 배치와 무관. 고정 주소 없음, 검증을 끄는 코드 없음, SSL_CERT_FILE로 사설 루트를 믿는 HTTPS 호출, DB 연결 없음(sslmode 해당 없음)."""
import ast
import http.server
import json
import re
import shutil
import ssl
import subprocess
import threading
from pathlib import Path

import pytest

from boundary_contracts import BoundaryError
from model_lab.wiring import factories
from tests.conftest import ROOT

SRC = [*sorted((ROOT / "model_lab").rglob("*.py")), *sorted((ROOT / "scripts").glob("*.py"))]


def _strings(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
            and n.body and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            yield n.lineno, n.value


def test_no_fixed_service_address_in_code():
    """다른 서비스를 부르는 주소는 설정(환경 변수)으로만 들어온다. 코드의 문자열에 http(s)://가 없다(주석·문서 문자열 제외)."""
    bad = [f"{p.relative_to(ROOT)}:{ln}: {s[:60]}" for p in SRC for ln, s in _strings(p) if re.search(r"https?://", s) and not s.startswith("https://openit.example/")]   # openit.example = JSON Schema $id 이름공간(예약 도메인, 접속하지 않는다)
    assert not bad, bad


def test_http_targets_in_shipped_configs_satisfy_the_contract_http_client_settings():
    """계약 4.0.0 _common.HttpClientSettings: 주소는 설정의 base_url(ServiceUrl: https 또는 loopback http만), 토큰은 비밀 이름(token_secret). 값은 설정에 없다."""
    import yaml
    found = []

    def walk(x):
        if isinstance(x, dict):
            if x.get("kind") == "http":
                found.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    for f in (ROOT / "configs").glob("*.yaml"):
        walk(yaml.safe_load(f.read_text(encoding="utf-8")))
    assert found
    import jsonschema
    from model_lab.wiring import schemas
    ref = {"$ref": "_common.schema.json#/$defs/HttpClientSettings"}
    v = jsonschema.Draft202012Validator(ref, resolver=jsonschema.RefResolver(schemas._BASE + "settings.json", ref, store=schemas._store()))
    for spec in found:
        errs = list(v.iter_errors(spec))
        assert not errs, (spec, errs[0].message)


def test_no_code_disables_certificate_verification():
    pat = re.compile(r"verify\s*=\s*False|CERT_NONE|check_hostname\s*=\s*False|_create_unverified_context|verify_mode\s*=")
    bad = [f"{p.relative_to(ROOT)}:{i}" for p in SRC + sorted(ROOT.glob("Dockerfile")) for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
           if pat.search(line)]
    assert not bad, bad


def test_no_database_connection_so_sslmode_does_not_apply():
    """이 영역의 저장은 파일시스템과 JSON이다(ADR 0002). DB 드라이버를 쓰지 않으니 접속 문자열·sslmode가 없다."""
    pat = re.compile(r"^\s*(import|from)\s+(psycopg|psycopg2|sqlalchemy|asyncpg|pymysql|sqlite3)\b", re.M)
    assert not [p for p in SRC if pat.search(p.read_text(encoding="utf-8"))]


# ---- SSL_CERT_FILE만으로 사설 루트를 믿는 HTTPS 호출 1건 ----
def _openssl(*args, cwd):
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True, timeout=60)


def _make_ca(d: Path, name: str):
    _openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{name}.key", "-out", f"{name}.pem", "-days", "2",
             "-subj", f"/CN={name}", "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign", cwd=d)


def _make_server_cert(d: Path, ca: str):
    (d / "san.ext").write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=CA:FALSE\n")
    _openssl("req", "-newkey", "rsa:2048", "-nodes", "-keyout", "srv.key", "-out", "srv.csr", "-subj", "/CN=localhost", cwd=d)
    _openssl("x509", "-req", "-in", "srv.csr", "-CA", f"{ca}.pem", "-CAkey", f"{ca}.key", "-CAcreateserial", "-out", "srv.pem", "-days", "2", "-extfile", "san.ext", cwd=d)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"items": ["pkg-a"], "next_cursor": None}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl CLI가 없다")
def test_https_call_trusts_private_root_via_ssl_cert_file_only(tmp_path, monkeypatch):
    _make_ca(tmp_path, "root-ok"); _make_ca(tmp_path, "root-other"); _make_server_cert(tmp_path, "root-ok")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tmp_path / "srv.pem", tmp_path / "srv.key")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    try:
        url = f"https://localhost:{srv.server_address[1]}"
        env = {"MODEL_REGISTRY_WRITE_TOKEN": "t" * 40}      # 토큰은 비밀 이름(token_secret)으로만 들어온다
        spec = {"kind": "http", "base_url": url, "token_secret": "MODEL_REGISTRY_WRITE_TOKEN", "timeout_seconds": 5}
        monkeypatch.delenv("SSL_CERT_DIR", raising=False)
        # 사설 루트를 신뢰하지 않으면 검증이 실패한다(검증이 켜져 있다는 증거)
        monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "root-other.pem"))
        with pytest.raises(BoundaryError) as e:
            factories.build_http("model_registry", spec, env).list_ids()
        assert e.value.code == "UNAVAILABLE"
        # SSL_CERT_FILE만으로 그 루트를 믿는다(코드에 CA 경로·검증 끄기 없음)
        monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "root-ok.pem"))
        items, nxt = factories.build_http("model_registry", spec, env).list_ids()
        assert items == ["pkg-a"] and nxt is None
    finally:
        srv.shutdown(); srv.server_close(); t.join(timeout=5)


def test_client_rejects_plain_http_outside_loopback():
    """계약 v3.4.1: 같은 기계(loopback) 밖의 http://는 생성 시 거부한다. 이 영역은 계약 클라이언트로만 다른 서비스를 부른다(직접 urllib 호출 없음)."""
    spec = {"kind": "http", "token_secret": "T", "timeout_seconds": 5}
    env = {"T": "t" * 40}
    with pytest.raises(ValueError):
        factories.build_http("model_registry", {**spec, "base_url": "http://model-registry:8080"}, env)
    factories.build_http("model_registry", {**spec, "base_url": "http://127.0.0.1:8080"}, env)
    factories.build_http("model_registry", {**spec, "base_url": "https://model-registry:8443"}, env)


def test_http_calls_use_the_contract_default_time_limits():
    """RUNTIME R10.3: 사슬 표에 없는 호출의 timeout은 30초, tar를 주고받는 I2·I3 게시·내려받기는 300초. 출하 설정의 http 대상은 그 값 이상을 넘지 않는다."""
    import yaml
    for f in (ROOT / "configs").glob("gpu-dev*.yaml"):
        c = yaml.safe_load(f.read_text(encoding="utf-8"))
        for spec in (c["inputs"].get("dataset_store"), c.get("registry")):
            if spec and spec.get("kind") == "http":
                assert spec["timeout_seconds"] == 300, (f.name, spec)       # 둘 다 tar 호출(I2 내려받기, I3 게시)
