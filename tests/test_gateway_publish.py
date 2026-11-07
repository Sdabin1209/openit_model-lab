"""U69: 학습 서버의 파이프라인이 게이트웨이(https, 기계 토큰 Bearer) 뒤의 model-registry에 게시할 수 있다.
우리 코드의 몫은 둘뿐이다: (1) 주소를 설정의 base_url로, 토큰을 token_secret의 NAME_FILE 파일로 받는다(D84) (2) TLS 신뢰는 SSL_CERT_FILE
(표준 OpenSSL/Python 환경 변수)로 정한다. TLS 종단은 게이트웨이의 몫이라 여기서는 시험용 종단 중계기를 앞에 둔다."""
import copy
import socket
import ssl
import subprocess
import threading

from tests.doubles.serve import serve, stop
from tests.test_pipeline import mk


def _self_signed(tmp_path):
    crt, key = tmp_path / "gw.crt", tmp_path / "gw.key"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(crt), "-days", "1",
                    "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"], check=True, capture_output=True)
    return crt, key


def _tls_front(crt, key, backend_port):
    """TLS를 풀어 평문 백엔드로 바이트를 그대로 넘기는 시험용 종단. (listener, port)"""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(crt, key)
    lst = socket.socket(); lst.bind(("127.0.0.1", 0)); lst.listen(8)

    def pipe(a, b):
        try:
            while (d := a.recv(65536)):
                b.sendall(d)
        except OSError:
            pass
        finally:
            try:
                b.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def serve_conn(raw):
        try:
            c = ctx.wrap_socket(raw, server_side=True)
            s = socket.create_connection(("127.0.0.1", backend_port))
        except OSError:
            raw.close(); return
        t = threading.Thread(target=pipe, args=(c, s), daemon=True); t.start()
        pipe(s, c); t.join(5); c.close(); s.close()

    def accept():
        while True:
            try:
                raw, _ = lst.accept()
            except OSError:
                return
            threading.Thread(target=serve_conn, args=(raw,), daemon=True).start()
    threading.Thread(target=accept, daemon=True).start()
    return lst, lst.getsockname()[1]


def test_publish_through_https_gateway_with_token_file_and_ca_file(cfg, tmp_path, monkeypatch):
    from model_lab.service.app import Service
    token = "w" * 40
    svc = Service(tmp_path / "remote", {"r" * 40: "release-gate"}, {token: "model-lab"}, log=lambda s: None)
    srv, port = serve(svc)
    crt, key = _self_signed(tmp_path)
    lst, tls_port = _tls_front(crt, key, port)
    try:
        tok = tmp_path / "write_token"
        tok.write_text(token + "\n")
        monkeypatch.delenv("GW_REGISTRY_TOKEN", raising=False)
        monkeypatch.setenv("GW_REGISTRY_TOKEN_FILE", str(tok))             # 비밀은 파일 경로로만 받는다
        monkeypatch.setenv("SSL_CERT_FILE", str(crt))                      # 게이트웨이 CA(여기서는 자체 서명)
        c = copy.deepcopy(cfg)
        c["registry"] = {"kind": "http", "base_url": f"https://localhost:{tls_port}", "token_secret": "GW_REGISTRY_TOKEN"}
        rec = mk(c).run()
        v = rec["result"]["packages"]["LORA_A"]["version"]
        assert svc.registry.get_manifest("lora-a-parser", v)["version"] == v
    finally:
        lst.close(); stop(srv)


def test_untrusted_gateway_certificate_is_refused(cfg, tmp_path, monkeypatch):
    """CA를 주지 않으면 검증을 끄지 않고 실패한다(인증서 검증 우회 금지)."""
    import pytest
    from boundary_contracts import BoundaryError
    from model_lab.service.app import Service
    token = "w" * 40
    svc = Service(tmp_path / "remote", {"r" * 40: "release-gate"}, {token: "model-lab"}, log=lambda s: None)
    srv, port = serve(svc)
    crt, key = _self_signed(tmp_path)
    lst, tls_port = _tls_front(crt, key, port)
    try:
        monkeypatch.setenv("GW_REGISTRY_TOKEN", token)
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        c = copy.deepcopy(cfg)
        c["registry"] = {"kind": "http", "base_url": f"https://localhost:{tls_port}", "token_secret": "GW_REGISTRY_TOKEN"}
        with pytest.raises(BoundaryError):
            mk(c).run()
        assert svc.registry.list_ids() == ([], None)
    finally:
        lst.close(); stop(srv)
