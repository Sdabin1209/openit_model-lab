"""U47: 게이트웨이 노출 선언(deploy-exposure.json, 이미지 라벨 io.openit.exposure, 계약 Exposure). 표와 실제 라우트가 일치하는지 본다."""
import json
import urllib.error
import urllib.request

import pytest
from boundary_contracts import validator

from model_lab.service.app import Service
from tests.conftest import ROOT
from tests.doubles.serve import serve, stop

EXPOSURE = ROOT / "deploy-exposure.json"


def load():
    return json.loads(EXPOSURE.read_text(encoding="utf-8"))


def test_exposure_file_satisfies_the_contract_and_denies_everything():
    ex = load()
    validator("Exposure").validate(ex)
    assert ex["service"] == "model-registry"
    # 왜 전부 denied: deploy/config/Caddyfile에 이 서비스로 가는 블록이 없다. release-gate가 내부망에서 직접 읽는다(GATE_I3_URL). 목록에 없는 /v1/*는 gateway가 404로 막는다.
    assert all(r["auth"]["kind"] == "denied" for r in ex["routes"])


@pytest.fixture
def server(tmp_path):
    svc = Service(tmp_path / "srv", {"rt": "release-gate"}, {"wt": "model-lab"}, log=lambda s: None)
    srv, port = serve(svc)
    yield f"http://127.0.0.1:{port}"
    stop(srv)


def call(url, method="GET", token="rt"):
    r = urllib.request.Request(url, method=method, data=b"x" if method == "POST" else None)
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def concrete(pattern: str) -> list[str]:
    """노출 표의 경로 패턴을 서버에 보낼 구체 경로로 바꾼다(* 는 한 구간, 끝의 /* 는 이 서비스의 실제 하위 경로들)."""
    return {"/v1/packages/*": ["/v1/packages/p/versions", "/v1/packages/p/versions/v1", "/v1/packages/p/versions/v1/adapter"],
            "/v1/eval-reports/*": ["/v1/eval-reports/r"]}.get(pattern, [pattern])


def test_every_declared_path_is_a_real_route_and_undeclared_paths_are_not(server):
    ex = load()
    for route in ex["routes"]:
        for pattern in route["paths"]:
            for path in concrete(pattern):
                status, body = call(server + path)
                assert "경로가 없다" not in body, f"{path}: 선언했지만 서버에 라우트가 없다"
    for path in ("/v1/other", "/v1/packages/p/versions/v1/other", "/v1/eval-reports"):
        status, body = call(server + path)
        assert status == 404 and "경로가 없다" in body, path           # 선언하지 않은 경로는 실제로도 없다


def test_declared_methods_cover_the_real_ones(server):
    ex = load()
    methods = {m for r in ex["routes"] for m in (r.get("methods") or ["GET", "POST", "PUT", "PATCH", "DELETE"])}
    assert {"GET", "POST"} <= methods                              # 서버가 GET(조회)과 POST(게시)를 받는다
    status, body = call(server + "/v1/packages", "PUT", "wt")
    assert status == 404 and "경로가 없다" in body                   # PUT 등은 서버도 거절한다(표는 그래도 denied라 노출 없음)
