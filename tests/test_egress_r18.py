"""RUNTIME R1.8 / CONVENTIONS 13절: 계약 클라이언트를 만드는 모든 곳이 OPENIT_EGRESS_PROXY를 egress_proxy로 넘긴다(가짜 CONNECT 프록시가 받은 줄로 확인).
게이트웨이가 상대를 허용하지 않으면 403 -> 클라이언트 PERMISSION_DENIED(details.reason=EGRESS_DENIED), 재시도 없음."""
import socketserver
import threading

import pytest
from boundary_contracts import BoundaryError

from model_lab.wiring import factories


class _Proxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        self.seen = []

        class H(socketserver.StreamRequestHandler):
            def handle(h):
                line = h.rfile.readline().decode()
                self.seen.append(line.strip())
                h.wfile.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
        super().__init__(("127.0.0.1", 0), H)


@pytest.fixture
def proxy():
    p = _Proxy()
    t = threading.Thread(target=p.serve_forever, daemon=True); t.start()
    yield p
    p.shutdown(); p.server_close(); t.join(timeout=5)


def env_for(p, **kw):
    return {"U": "https://dataset-store:8443", "T": "t" * 40, "OPENIT_EGRESS_PROXY": f"http://127.0.0.1:{p.server_address[1]}", **kw}


SPEC = {"kind": "http", "base_url_env": "U", "token_env": "T", "timeout": 5}


def call(c):
    for name in ("list_ids", "list_versions", "list_cases"):
        if hasattr(c, name):
            return getattr(c, name)("x") if name == "list_versions" else getattr(c, name)()


@pytest.mark.parametrize("kind,target", [("model_registry", "model-registry"), ("dataset_store", "dataset-store"), ("failure_store", "ops-feedback")])
def test_build_http_clients_tunnel_through_the_egress_proxy(proxy, kind, target):
    env = env_for(proxy, U=f"https://{target}:8443")
    with pytest.raises(BoundaryError) as e:
        call(factories.build_http(kind, SPEC, env))
    assert e.value.code == "PERMISSION_DENIED" and (e.value.details or {}).get("reason") == "EGRESS_DENIED"
    assert proxy.seen == [f"CONNECT {target}:8443 HTTP/1.1"]          # 한 번만(재시도 없음), 상대 이름 그대로


def test_factory_built_clients_tunnel_too(proxy):
    c = factories.build({"factory": "boundary_contracts.clients:HttpDatasetStore", "args": {"base_url_env": "U", "token_env": "T", "timeout": 5}}, env=env_for(proxy))
    with pytest.raises(BoundaryError) as e:
        c.list_versions("d")
    assert e.value.code == "PERMISSION_DENIED" and proxy.seen == ["CONNECT dataset-store:8443 HTTP/1.1"]


def test_non_loopback_proxy_is_refused_at_creation():
    with pytest.raises(ValueError):
        factories.build_http("model_registry", SPEC, {"U": "https://model-registry:8443", "T": "t" * 40, "OPENIT_EGRESS_PROXY": "http://proxy.example:3128"})
