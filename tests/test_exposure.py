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


def test_exposure_file_satisfies_the_contract_and_exposes_only_publish():
    ex = load()
    validator("Exposure").validate(ex)
    assert ex["service"] == "model-registry"
    # 왜 게시만 machine-token: 학습 서버(다른 호스트)의 파이프라인이 게이트웨이(https)를 거쳐 POST /v1/packages로 게시한다(U69). 게이트웨이가 Bearer를 비밀
    # model-registry-write-model-lab과 대조하고 상류에는 레지스트리 쓰기 토큰을 붙인다. 읽기 경로는 release-gate가 내부망에서 직접 읽으므로(GATE_I3_URL) 전부 denied.
    open_routes = [r for r in ex["routes"] if r["auth"]["kind"] != "denied"]
    assert len(open_routes) == 1
    r = open_routes[0]
    assert r["paths"] == ["/v1/packages"] and r["methods"] == ["POST"] and r["auth"]["kind"] == "machine-token"
    assert r["upstream_token"] == "model-registry-write-model-lab"
    # 기계 입구에는 사람 계정이 없으므로 게이트웨이가 X-Openit-User를 지운다(다른 machine-token 줄과 같다)
    assert r["user_header"] == "strip"
    # 같은 경로의 다른 메서드는 denied로 명시한다(메서드를 비운 선언이 게시 선언과 겹치지 않게)
    assert any(x["paths"] == ["/v1/packages"] and "GET" in x["methods"] and x["auth"]["kind"] == "denied" for x in ex["routes"])


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
    assert status == 405                                          # PUT 등은 서버도 거절한다(link 규약: 405 + Allow. 이전에는 404였다). 표는 그래도 denied라 노출 없음


def test_egress_lists_exactly_the_neighbours_model_lab_calls_per_interfaces():
    """CONVENTIONS 13절, RUNTIME R1.8: 영역이 부르는 상대 전부. INTERFACES 한눈에 표에서 model-lab이 읽는 I2·I1·I7·I4. I9(추론 서버)는 HTTP로 부르지 않는다(3.4.6)."""
    ex = load()
    assert sorted(ex["egress"]) == ["dataset-store", "ops-feedback", "question-authoring", "release-gate"]
    assert "model-serving" not in ex["egress"] and "model-registry" not in ex["egress"]
    assert any("/metrics" in r["paths"] and r["auth"]["kind"] == "denied" for r in ex["routes"])     # R10.11: /metrics는 바깥 입구에 열지 않는다


def test_publish_route_declares_the_body_limit_the_service_enforces():
    """RUNTIME R10.13, Exposure.max_body_bytes(3.4.7): 게이트웨이가 413을 낼 상한 = 서비스의 MODEL_REGISTRY_MAX_BODY 기본값(4GiB, ADR 0007·0034), 8GiB 이하."""
    import boundary_contracts, json as _j, os
    env = _j.load(open(os.path.join(os.path.dirname(boundary_contracts.__file__), "schemas", "ModelRegistryEnv.schema.json"), encoding="utf-8"))
    default = int(env["properties"]["MODEL_REGISTRY_MAX_BODY"]["default"])
    pub = [r for r in load()["routes"] if r["auth"]["kind"] == "machine-token"][0]
    assert pub["max_body_bytes"] == default == 4 << 30 and pub["max_body_bytes"] <= 8 << 30
