"""I3 적합성 시험: 파일 구현과 메모리 구현이 같은 시험을 통과한다(CONVENTIONS 4절, INTERFACES I3)."""
import copy
import json

import pytest
from boundary_contracts import BoundaryError, canonical
from boundary_contracts.sdk import ModelRegistry

from model_lab.registry import FileModelRegistry, MemoryModelRegistry


class _HttpReg:
    """서버(별도 스레드)에 붙은 HTTP 클라이언트. _tamper_adapter는 서버 쪽 저장소를 직접 바꾼다."""
    def __init__(self, client, backing):
        self._c, self._b = client, backing
    def __getattr__(self, n):
        return getattr(self._c, n)
    def _tamper_adapter(self, pid, ver):
        self._b._tamper_adapter(pid, ver)


@pytest.fixture(params=["memory", "file", "http"])
def reg(request, tmp_path):
    if request.param == "memory":
        return MemoryModelRegistry()
    if request.param == "file":
        return FileModelRegistry(tmp_path / "reg")
    import threading
    from model_lab.service.app import Service, make_server
    from boundary_contracts.clients import HttpModelRegistry
    svc = Service(tmp_path / "srv", {"rt": "release-gate"}, {"wt": "model-lab"}, log=lambda s: None)
    srv = make_server(svc)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    request.addfinalizer(srv.shutdown)
    return _HttpReg(HttpModelRegistry(f"http://127.0.0.1:{srv.server_address[1]}", "wt"), svc.registry)


def make(tmp_path, pid="pkg-a", ver="v1", payload=b"w", report=b"report-1", ref="eval-1"):
    ad = tmp_path / f"ad-{pid}-{ver}"
    (ad / "sub").mkdir(parents=True, exist_ok=True)
    (ad / "adapter_model.bin").write_bytes(payload)
    (ad / "sub" / "c.json").write_text("{}")
    (ad / "system_prompt.txt").write_bytes(b"system prompt for " + pid.encode())
    sc = {"prompt_template_version": "prompt-2", "decoding": {"constraint_mode": "none", "temperature": 0, "seed": 1, "max_tokens": 8},
          "max_attempts": 3, "constraint": {"backend": "x", "backend_version": "1", "supported_features": [], "relax_policy_version": "relax-3"},
          "output_contract": 1, "protection_policy_version": "protect-1"}
    h = "sha256:" + "a" * 64
    m = {"pipeline_schema_version": "2.0", "package_id": pid, "version": ver, "role": "LORA_A", "created_at": "2026-10-15T00:00:00Z",
         "runtime_contract_version": 1, "base_model": {"id": "base", "revision": "r"}, "adapter_hash": canonical.directory_hash(ad),
         "serving_config": sc, "serving_config_hash": canonical.sha256_hash(sc),
         "system_prompt": {"path": "system_prompt.txt", "sha256": canonical.file_hash(ad / "system_prompt.txt")},
         "lineage": {"dataset": {"id": "d", "version": "1", "content_hash": h}, "training_run_id": "run-1",
                     "turn_engine_version": "0.2.0", "model_runtime_version": "0.3.0"},
         "eval_report_ref": ref}
    m["content_hash"] = canonical.content_hash(m)
    return m, ad, report


def code(fn, *a, **k):
    with pytest.raises(BoundaryError) as e:
        fn(*a, **k)
    return e.value.code


def test_protocol(reg):
    assert isinstance(getattr(reg, '_c', reg), ModelRegistry)


def test_publish_and_read(reg, tmp_path):
    m, ad, rep = make(tmp_path)
    assert reg.publish(m, ad, rep) == m
    assert reg.get_manifest("pkg-a", "v1") == m
    assert reg.get_eval_report("eval-1") == rep
    out = reg.open_adapter("pkg-a", "v1", dest=tmp_path / "out")
    assert canonical.directory_hash(out) == m["adapter_hash"]


def test_idempotent_and_conflict(reg, tmp_path):
    m, ad, rep = make(tmp_path)
    reg.publish(m, ad, rep)
    assert reg.publish(copy.deepcopy(m), ad, rep) == m                      # 같은 내용: 성공, 기존 것
    assert reg.list_versions("pkg-a") == ["v1"]
    m2, ad2, rep2 = make(tmp_path, payload=b"other")                      # 같은 (id, version), 다른 내용
    assert code(reg.publish, m2, ad2, rep2) == "CONFLICT"
    assert reg.get_manifest("pkg-a", "v1") == m


def test_eval_report_conflict(reg, tmp_path):
    m, ad, rep = make(tmp_path)
    reg.publish(m, ad, rep)
    m2, ad2, _ = make(tmp_path, ver="v2", payload=b"p2")
    assert code(reg.publish, m2, ad2, b"different") == "CONFLICT"           # 같은 ref에 다른 바이트
    assert reg.list_versions("pkg-a") == ["v1"]                             # 실패한 게시는 흔적이 없다


def test_validation_errors(reg, tmp_path):
    m, ad, rep = make(tmp_path)
    bad = copy.deepcopy(m); del bad["lineage"]
    assert code(reg.publish, bad, ad, rep) == "INVALID_RECORD"
    bad = copy.deepcopy(m); bad["version"] = "bad/version"
    bad["content_hash"] = canonical.content_hash(bad)
    assert code(reg.publish, bad, ad, rep) == "INVALID_RECORD"
    bad = copy.deepcopy(m); bad["role"] = "LORA_A"; bad["serving_config_hash"] = "sha256:" + "b" * 64
    bad["content_hash"] = canonical.content_hash(bad)
    assert code(reg.publish, bad, ad, rep) == "INVALID_RECORD"
    bad = copy.deepcopy(m); bad["created_at"] = "2026-10-16T00:00:00Z"        # content_hash를 다시 계산하지 않음
    assert code(reg.publish, bad, ad, rep) == "INTEGRITY_ERROR"
    (ad / "adapter_model.bin").write_bytes(b"tampered")                        # adapter_hash 불일치
    assert code(reg.publish, m, ad, rep) == "INTEGRITY_ERROR"
    assert code(reg.publish, m, tmp_path / "nope", rep) in ("NOT_FOUND", "INVALID_RECORD")      # 원격 클라이언트는 INVALID_RECORD


def test_not_found(reg, tmp_path):
    assert code(reg.get_manifest, "x", "v1") == "NOT_FOUND"
    assert code(reg.open_adapter, "x", "v1", dest=tmp_path) == "NOT_FOUND"
    assert code(reg.get_eval_report, "nope") == "NOT_FOUND"
    assert reg.list_versions("x") == []


def test_lists_and_paging(reg, tmp_path):
    for pid in ("pkg-b", "pkg-a", "pkg-c"):
        for ver in ("v2", "v1", "v3"):                                        # 게시 순서가 곧 오래된 순서
            m, ad, rep = make(tmp_path, pid=pid, ver=ver, payload=f"{pid}{ver}".encode(), ref=f"eval-{pid}-{ver}", report=f"{pid}{ver}".encode())
            reg.publish(m, ad, rep)
    assert reg.list_versions("pkg-a") == ["v2", "v1", "v3"]
    ids, cur, seen = [], None, []
    while True:
        page, cur = reg.list_ids(cursor=cur, limit=2)
        seen.append(len(page)); ids += page
        if cur is None:
            break
    assert ids == ["pkg-b", "pkg-a", "pkg-c"] and seen == [2, 1]
    m, ad, rep = make(tmp_path, pid="pkg-d", ver="v1", payload=b"d", ref="eval-d", report=b"d")
    reg.publish(m, ad, rep)
    assert reg.list_ids()[0] == ["pkg-b", "pkg-a", "pkg-c", "pkg-d"]          # 새 항목은 뒤에
    assert code(reg.list_ids, limit=0) == "INVALID_RECORD"
    assert code(reg.list_ids, cursor="abc") == "INVALID_RECORD"


def test_open_adapter_detects_tampering(reg, tmp_path):
    m, ad, rep = make(tmp_path)
    reg.publish(m, ad, rep)
    reg._tamper_adapter("pkg-a", "v1")
    assert code(reg.open_adapter, "pkg-a", "v1", dest=tmp_path / "o") == "INTEGRITY_ERROR"


def test_manifest_tampering_detected_on_read(tmp_path):
    r = FileModelRegistry(tmp_path / "reg")
    m, ad, rep = make(tmp_path)
    r.publish(m, ad, rep)
    p = tmp_path / "reg" / "manifests" / "pkg-a" / "v1.json"
    d = json.loads(p.read_text()); d["role"] = "LORA_B"; p.write_text(json.dumps(d))
    assert code(r.get_manifest, "pkg-a", "v1") == "INTEGRITY_ERROR"


def test_file_registry_survives_reopen(tmp_path):
    r = FileModelRegistry(tmp_path / "reg")
    m, ad, rep = make(tmp_path)
    r.publish(m, ad, rep)
    r2 = FileModelRegistry(tmp_path / "reg")
    assert r2.get_manifest("pkg-a", "v1") == m and r2.list_ids() == (["pkg-a"], None)


def test_system_prompt_is_part_of_package_and_checked(reg, tmp_path):
    """D75: 시스템 프롬프트는 패키지의 일부다. 매니페스트에 없거나, 파일이 없거나, 해시가 다르면 게시할 수 없다."""
    m, ad, rep = make(tmp_path)
    no_ref = copy.deepcopy(m); del no_ref["system_prompt"]
    no_ref["content_hash"] = canonical.content_hash(no_ref)
    assert code(reg.publish, no_ref, ad, rep) == "INVALID_RECORD"                        # 필수 필드
    wrong = copy.deepcopy(m); wrong["system_prompt"]["sha256"] = "sha256:" + "0" * 64    # 파일 해시와 다름
    wrong["content_hash"] = canonical.content_hash(wrong)
    assert code(reg.publish, wrong, ad, rep) == "INTEGRITY_ERROR"
    missing = copy.deepcopy(m); missing["system_prompt"]["path"] = "nope.txt"            # 어댑터 안에 없는 파일
    missing["content_hash"] = canonical.content_hash(missing)
    assert code(reg.publish, missing, ad, rep) == "INTEGRITY_ERROR"
    assert reg.publish(m, ad, rep) == m                                                  # 올바른 것은 게시된다
