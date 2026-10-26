"""회귀 평가(I7, 실제 HttpFailureStore + 작은 서버 대역)와 파일 데이터셋 저장소 읽기 시험."""
import copy
import json
import shutil
from pathlib import Path

import pytest
from boundary_contracts import PIPELINE_SCHEMA_VERSION, BoundaryError, canonical
from boundary_contracts.sdk import DatasetStore

from model_lab.file_dataset_store import FileDatasetReader, file_dataset_store
from model_lab.pipeline import Run, run_once
from tests.conftest import FIXED, SCHEMA_DIR
from tests.doubles.failure_server import FailureServer
from tests.doubles.fixtures import load_samples


def case(cid, expected, **kw):
    c = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "case_id": cid, "version": 1, "created_at": "2026-10-15T00:00:00Z",
         "source_release_id": "rel-1", "runtime_contract_version": 1, "deidentified": True, "deidentified_trace_ref": f"tr-{cid}",
         "failure_type": "ft-a", "detection_method": "HUMAN", "review_status": "CONFIRMED"}
    if expected is not None:
        c["expected"] = expected
    c.update(kw)
    return c


@pytest.fixture
def failure_world():
    P = load_samples("LORA_A")
    G = load_samples("LORA_B")
    cases = [case("c-parser", P[0]["expected"], expected_kind="LORA_A", failure_type="ft-a"),
             case("c-gen-noKind", G[0]["expected"], failure_type="ft-b"),                      # expected_kind 없음: 형태(문자열)로 판별
             case("c-pending", P[1]["expected"], review_status="PENDING"),
             case("c-noinput", P[2]["expected"], expected_kind="LORA_A"),
             case("c-notrace", P[3]["expected"], expected_kind="LORA_A"),
             case("c-noexpected", None),
             case("c-weird", {"foo": 1}, expected_kind="LORA_A")]      # 정답이 ActionExtraction을 지키지 않음
    # trace 안의 입력 키 이름은 추정이다(regression._TRACE_KEYS, 미정)
    traces = {"tr-c-parser": {"extract_input": P[0]["input"]}, "tr-c-gen-noKind": {"response_input": G[0]["input"]},
              "tr-c-pending": {"extract_input": P[1]["input"]}, "tr-c-noinput": {"extract_input": None}, "tr-c-weird": {"extract_input": P[4]["input"]}}
    fs = FailureServer(cases, traces)
    yield fs
    fs.stop()


def reg_cfg(cfg, fs, monkeypatch, **reg):
    monkeypatch.setenv("TEST_FAILURE_TOKEN", fs.token)
    c = copy.deepcopy(cfg)
    c["evaluation"]["regression"] = {"enabled": True, "label": "t1", **reg}
    c["inputs"]["failure_store"] = {"factory": "boundary_contracts.clients:HttpFailureStore",
                                    "args": {"base_url": fs.url, "token_env": "TEST_FAILURE_TOKEN"}}
    return c


def test_regression_section_in_report(cfg, failure_world, monkeypatch):
    c = reg_cfg(cfg, failure_world, monkeypatch)
    r = Run(c, clock=FIXED, log=lambda s: None); r.run()
    ra = json.loads((r.dir / "eval/report-LORA_A.json").read_text())["regression"]
    rb = json.loads((r.dir / "eval/report-LORA_B.json").read_text())["regression"]
    assert ra["enabled"] and ra["cases_total"] == 5 and ra["used_for_role"] == 1 and rb["used_for_role"] == 1     # CONFIRMED + expected 있는 것만
    assert ra["skipped"]["trace_input_missing"]["count"] == 1 and ra["skipped"]["trace_not_found"]["count"] == 1
    assert ra["skipped"]["expected_invalid"]["count"] == 1
    assert ra["aggregate"]["prot_exact"] == 1 and ra["aggregate"]["raw_valid"] == 1                       # 오라클 모델이라 일치
    assert set(ra["by_failure_type"]) == {"ft-a"} and set(rb["by_failure_type"]) == {"ft-b"}
    assert "threshold" not in json.dumps(ra).lower().replace("임계치", "")


def test_regression_off_by_default(cfg):
    r = Run(cfg, clock=FIXED, log=lambda s: None); r.run()
    assert json.loads((r.dir / "eval/report-LORA_A.json").read_text())["regression"] == {"enabled": False}


def test_regression_requires_failure_store_when_enabled(cfg):
    from model_lab.errors import ConfigError
    c = copy.deepcopy(cfg); c["evaluation"]["regression"] = {"enabled": True}
    with pytest.raises(ConfigError):
        Run(c, clock=FIXED, log=lambda s: None).run()


def test_regression_store_outage_stops_with_retryable_error(cfg, failure_world, monkeypatch):
    c = reg_cfg(cfg, failure_world, monkeypatch)
    failure_world.down = True
    with pytest.raises(BoundaryError) as e:
        Run(c, clock=FIXED, log=lambda s: None).run()
    assert e.value.code == "UNAVAILABLE"
    failure_world.down = False
    assert Run(c, clock=FIXED, log=lambda s: None).run()["result"]["status"] == "complete"      # 학습은 남아 있고 평가부터 이어 간다


def test_regression_label_makes_new_run(cfg, failure_world, monkeypatch):
    a = Run(reg_cfg(cfg, failure_world, monkeypatch, label="x"), clock=FIXED, log=lambda s: None)
    b = Run(reg_cfg(cfg, failure_world, monkeypatch, label="y"), clock=FIXED, log=lambda s: None)
    assert a.run_id != b.run_id


# ---- 파일 데이터셋 저장소 읽기 ----
def to_layout(src: Path, dest: Path) -> None:
    """tests의 DirDatasetStore 배치를 synthetic-data의 파일 배치(manifest.json, meta.json, files/)로 옮긴다."""
    for ds in src.iterdir():
        versions = json.loads((ds / "versions.json").read_text())
        for seq, v in enumerate(versions, 1):
            d = dest / ds.name / v
            shutil.copytree(ds / v, d / "files")
            (d / "files" / "manifest.json").rename(d / "manifest.json")
            (d / "meta.json").write_text(json.dumps({"seq": seq}))


@pytest.fixture
def layout(tmp_path, fixture_store):
    root = tmp_path / "layout"
    to_layout(fixture_store, root)
    return root


def test_file_reader_follows_protocol_and_layout(layout):
    st = FileDatasetReader(layout)
    assert isinstance(st, DatasetStore)
    assert st.list_versions("fx-golden") == ["v1"] and st.list_versions("nope") == []
    m = st.get_manifest("fx-golden", "v1")
    with st.open_file("fx-golden", "v1", m["files"][0]["path"]) as f:
        assert canonical.file_hash(layout / "fx-golden/v1/files" / m["files"][0]["path"]) == m["files"][0]["content_hash"] and f.read(1)


def test_file_reader_errors_and_order(layout, tmp_path):
    from tests.doubles.fixtures import build_dataset
    st = FileDatasetReader(layout)
    for code, fn in (("NOT_FOUND", lambda: st.get_manifest("fx-golden", "v9")), ("NOT_FOUND", lambda: st.open_file("fx-golden", "v1", "zz.jsonl")),
                     ("INVALID_RECORD", lambda: st.get_manifest("../x", "v1")), ("PERMISSION_DENIED", lambda: st.publish({}, {}))):
        with pytest.raises(BoundaryError) as e:
            fn()
        assert e.value.code == code
    m = st.get_manifest("fx-golden", "v1")
    p = layout / "fx-golden/v1/files" / m["files"][0]["path"]
    p.write_bytes(p.read_bytes() + b"\n")                                    # 파일 변조
    with pytest.raises(BoundaryError) as e:
        st.open_file("fx-golden", "v1", m["files"][0]["path"])
    assert e.value.code == "INTEGRITY_ERROR"
    mp = layout / "fx-golden/v1/manifest.json"
    d = json.loads(mp.read_text()); d["generator_version"] = "x"; mp.write_text(json.dumps(d))        # 매니페스트 변조
    with pytest.raises(BoundaryError) as e:
        st.get_manifest("fx-golden", "v1")
    assert e.value.code == "INTEGRITY_ERROR"


def test_file_reader_orders_by_seq_and_skips_partial(tmp_path):
    root = tmp_path / "r"
    for name, seq in (("b-new", 2), ("a-old", 1), ("c-newest", 3)):
        d = root / "ds" / name; d.mkdir(parents=True)
        (d / "manifest.json").write_text("{}"); (d / "meta.json").write_text(json.dumps({"seq": seq}))
    (root / "ds" / ".tmp-x").mkdir(); (root / "ds" / "half").mkdir()          # 임시·반쯤 쓴 버전
    assert FileDatasetReader(root).list_versions("ds") == ["a-old", "b-new", "c-newest"]


def test_pipeline_reads_from_file_layout(cfg, layout, monkeypatch):
    monkeypatch.setenv("TEST_DATASET_ROOT", str(layout))
    c = copy.deepcopy(cfg)
    c["inputs"]["dataset_store"] = {"factory": "model_lab.file_dataset_store:file_dataset_store", "args": {"root_env": "TEST_DATASET_ROOT"}}
    assert run_once(c, clock=FIXED, log=lambda s: None)["action"] == "ran"
