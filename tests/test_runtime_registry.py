"""RUNTIME 조항 시험(model-registry 서비스, 출력하는 쪽): R5.1 HealthStatus, R10.11 /metrics, R3.10 저장소 쓰기 불가 503, R4.1 복구, R10.8 게시 원자성, R10.13 본문 상한 413.
모두 실제 HTTP로 상태 코드 + 본문(+필요한 헤더)을 단언한다."""
import errno
import json
import shutil
import urllib.error
import urllib.request

import pytest
from boundary_contracts import BoundaryError, validator
from boundary_contracts.clients import HttpModelRegistry

from model_lab.components.model_registry import FileModelRegistry
from model_lab.service.app import Service
from tests.doubles.serve import serve, stop
from tests.test_registry import make


def http(url, method="GET", token=None, body=None):
    r = urllib.request.Request(url, data=body, method=method)
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


@pytest.fixture
def srv(tmp_path):
    svc = Service(tmp_path / "srv", {"rt": "release-gate"}, {"wt": "model-lab"}, log=lambda s: None)
    server, port = serve(svc)
    yield svc, f"http://127.0.0.1:{port}"
    stop(server)


# ---- R5.1 ----
def test_healthz_and_readyz_bodies_are_health_status(srv):
    svc, url = srv
    for path, status in (("/healthz", "ok"), ("/readyz", "ready")):
        s, _, b = http(url + path)
        assert s == 200 and json.loads(b)["status"] == status
        validator("HealthStatus").validate(json.loads(b))


def test_readyz_503_body_says_not_ready_with_the_new_spelling(srv):
    svc, url = srv
    shutil.rmtree(svc.tmp)                         # 저장소 쓰기 불가를 흉내(임시 폴더 없음)
    s, _, b = http(url + "/readyz")
    assert s == 503 and json.loads(b) == {"status": "not_ready"}       # 'not-ready'는 옛 표기
    validator("HealthStatus").validate(json.loads(b))
    s, _, b = http(url + "/healthz")
    assert s == 200                                  # 살아 있음과 준비됨은 따로다


# ---- R10.11 ----
def test_metrics_is_prometheus_text_without_auth_and_survives_storage_outage(srv):
    svc, url = srv
    http(url + "/v1/packages", token="rt")
    s, h, b = http(url + "/metrics")
    assert s == 200 and h["Content-Type"].startswith("text/plain") and b"model_registry_http_requests_total" in b
    s2, _, _ = http(url + "/metrics", token="wrong-token")
    assert s2 == 200                                                # 토큰이 있어도 무시(401·403 없음)
    shutil.rmtree(svc.tmp)                                          # 저장소 장애 중에도 200(프로세스 안의 값만)
    assert http(url + "/readyz")[0] == 503 and http(url + "/metrics")[0] == 200


# ---- R3.10 + R4.1 ----
def test_storage_write_failure_is_503_unavailable_then_recovers_without_restart(srv, tmp_path, monkeypatch):
    svc, url = srv
    m, ad, rep = make(tmp_path)
    real = FileModelRegistry._atomic_write

    def enospc(p, data):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(FileModelRegistry, "_atomic_write", staticmethod(enospc))
    w = HttpModelRegistry(url, "wt", timeout=10)
    with pytest.raises(BoundaryError) as e:
        w.publish(m, ad, rep)
    assert e.value.code == "UNAVAILABLE" and e.value.retryable                    # 서버가 503 + UNAVAILABLE
    assert HttpModelRegistry(url, "rt").list_versions("pkg-a") == []               # 쓰다 만 흔적 없음
    monkeypatch.setattr(FileModelRegistry, "_atomic_write", staticmethod(real))     # 복구(재시작 없음)
    assert http(url + "/readyz")[0] == 200
    assert w.publish(m, ad, rep)["package_id"] == "pkg-a"
    assert HttpModelRegistry(url, "rt").list_versions("pkg-a") == ["v1"]


def test_storage_read_failure_is_503_and_readyz_503_then_recovers(srv, tmp_path, monkeypatch):
    svc, url = srv

    def boom(*a, **k):
        raise OSError(errno.EIO, "I/O error")
    monkeypatch.setattr(FileModelRegistry, "list_ids", boom)
    s, _, b = http(url + "/v1/packages", token="rt")
    assert s == 503 and json.loads(b)["code"] == "UNAVAILABLE"
    assert http(url + "/readyz")[0] == 503
    monkeypatch.undo()
    assert http(url + "/v1/packages", token="rt")[0] == 200 and http(url + "/readyz")[0] == 200


# ---- R10.8 ----
@pytest.mark.parametrize("fail_on", ["/v1.json", "_order.json", "ids.json", "eval_reports", ".published.json"])
def test_failed_publish_leaves_no_readable_trace(tmp_path, monkeypatch, fail_on):
    reg = FileModelRegistry(tmp_path / "r")
    m, ad, rep = make(tmp_path)
    real = FileModelRegistry._atomic_write

    def flaky(p, data):
        if str(p).endswith(fail_on) or (fail_on == 'eval_reports' and fail_on in str(p)):
            raise OSError(errno.ENOSPC, "full")
        return real(p, data)
    monkeypatch.setattr(FileModelRegistry, "_atomic_write", staticmethod(flaky))
    with pytest.raises(OSError):
        reg.publish(m, ad, rep, publisher={"role": "model-lab", "remote": "x", "published_at": "2026-11-04T00:00:00Z"})
    monkeypatch.undo()
    assert reg.list_ids()[0] == [] and reg.list_versions("pkg-a") == []
    for call in (lambda: reg.get_manifest("pkg-a", "v1"), lambda: reg.get_eval_report("eval-1")):
        with pytest.raises(BoundaryError) as e:
            call()
        assert e.value.code == "NOT_FOUND"
    assert not (tmp_path / "r" / "adapters" / "pkg-a" / "v1").exists()
    assert reg.publish(m, ad, rep) == m                              # 다시 보내면 정상(201 상당)


def test_republish_same_content_is_200_and_different_is_409_over_http(srv, tmp_path):
    svc, url = srv
    w = HttpModelRegistry(url, "wt")
    m, ad, rep = make(tmp_path)
    assert w.publish(m, ad, rep) == m and w.publish(m, ad, rep) == m
    m2, ad2, rep2 = make(tmp_path, payload=b"other")
    m2["version"] = "v1"
    from boundary_contracts import canonical
    m2.pop("content_hash"); m2["content_hash"] = canonical.content_hash(m2)
    with pytest.raises(BoundaryError) as e:
        w.publish(m2, ad2, rep2)
    assert e.value.code == "CONFLICT"


# ---- R10.13 ----
def test_publish_body_over_the_limit_is_413_invalid_record_and_not_retried(tmp_path):
    svc = Service(tmp_path / "srv", {"rt": "release-gate"}, {"wt": "model-lab"}, log=lambda s: None, max_body=4096)
    server, port = serve(svc)
    try:
        m, ad, rep = make(tmp_path, payload=b"x" * 20000)
        w = HttpModelRegistry(f"http://127.0.0.1:{port}", "wt", timeout=10)
        with pytest.raises(BoundaryError) as e:
            w.publish(m, ad, rep)
        assert e.value.code == "INVALID_RECORD" and (e.value.details or {}).get("http_status") == 413 and not e.value.retryable
        assert HttpModelRegistry(f"http://127.0.0.1:{port}", "rt").list_ids()[0] == []      # 아무것도 저장되지 않음
    finally:
        stop(server)


# ---- I4 판정 조회(notice-346 3) ----
class _FakeReader:
    def __init__(self, by_key):
        self.by_key, self.calls = by_key, []

    def find_decisions(self, *, lora_a=None, lora_b=None, cursor=None, limit=100):
        key = ("lora_a", lora_a["id"]) if lora_a else ("lora_b", lora_b["id"])
        self.calls.append((key, cursor))
        return self.by_key.get(key, [])[:], None


def _decision(i, result="PASS"):
    return {"decision_id": i, "result": result, "decided_at": "2026-11-04T00:00:00Z"}


def test_record_decisions_puts_the_gate_decision_into_the_run_record_and_never_submits(cfg):
    from model_lab.wiring.pipeline import Run
    from tests.conftest import FIXED
    reader = _FakeReader({("lora_a", "lora-a-parser"): [_decision("d1"), _decision("d2", "REJECT")], ("lora_b", "lora-b-generator"): [_decision("d1")]})
    r = Run(cfg, clock=FIXED, log=lambda s: None, release_reader=reader)
    rec = r.run()
    d = rec["decisions"]
    assert d["status"] == "found" and {x["decision_id"]: (x["result"], x["matches"]) for x in d["items"]} == {"d1": ("PASS", ["LORA_A", "LORA_B"]), "d2": ("REJECT", ["LORA_A"])}
    assert not hasattr(reader, "submit") and all(c[0][0] in ("lora_a", "lora_b") for c in reader.calls)


def test_decisions_status_none_unavailable_and_not_configured(cfg):
    from boundary_contracts import BoundaryError
    from model_lab.wiring.pipeline import Run
    from tests.conftest import FIXED
    assert Run(cfg, clock=FIXED, log=lambda s: None).run()["decisions"] == {"status": "not_configured"}

    class Down:
        def find_decisions(self, **k):
            raise BoundaryError("UNAVAILABLE", "down")
    import copy
    c2 = copy.deepcopy(cfg); c2["run"]["seed"] = 77
    rec = Run(c2, clock=FIXED, log=lambda s: None, release_reader=Down()).run()
    assert rec["result"]["status"] == "complete" and rec["decisions"] == {"status": "unavailable", "code": "UNAVAILABLE"}   # 조회 실패가 실행을 실패시키지 않는다
    c3 = copy.deepcopy(cfg); c3["run"]["seed"] = 78
    assert Run(c3, clock=FIXED, log=lambda s: None, release_reader=_FakeReader({})).run()["decisions"] == {"status": "none", "items": []}


# ---- ModelPackageManifest.lineage.question_sets (RUNTIME R10.8 보충, 계약 3.4.4 B-04) ----
def test_manifest_lineage_question_sets_is_filled_and_empty_is_refused():
    from model_lab.components.package_build import training_question_sets
    dm = {"question_sets": [{"id": "qs", "version": "1", "content_hash": "sha256:" + "b" * 64}],
          "files": [{"role": "LORA_A", "split": "TRAIN"}, {"role": "LORA_A", "split": "EVAL"}]}
    assert training_question_sets(dm, "LORA_A") == dm["question_sets"]
    with pytest.raises(BoundaryError) as e:
        training_question_sets({"question_sets": [], "files": dm["files"]}, "LORA_A")
    assert e.value.code == "INVALID_RECORD"
