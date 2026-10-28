"""파이프라인(dry-run 학습기 + fake-oracle 평가) 시험: 재현성, 재개, 멱등, 누수 차단, 보고 구조."""
import copy
import json
import shutil

import pytest
from boundary_contracts import BoundaryError, canonical, validator

from model_lab.config import ConfigError, load_config, validate_config
from model_lab.errors import PipelineError
from model_lab.pipeline import Run, run_once
from model_lab.trainers.dryrun import DryRunTrainer, Interrupted
from tests.conftest import FIXED
from tests.doubles.serve import stop
from tests.doubles.dataset_store import DirDatasetStore


def mk(cfg, **kw):
    return Run(cfg, clock=FIXED, log=lambda s: None, **kw)


def with_dirs(cfg, tmp_path, name):
    c = copy.deepcopy(cfg)
    c["run"]["runs_dir"] = str(tmp_path / name / "runs")
    c["registry"]["path"] = str(tmp_path / name / "reg")
    return c


def test_runs_to_publication(cfg):
    r = mk(cfg)
    rec = r.run()
    assert rec["result"]["status"] == "complete"
    pk = rec["result"]["packages"]
    for role, pid in (("LORA_A", "lora-a-parser"), ("LORA_B", "lora-b-generator")):
        m = r.registry.get_manifest(pid, pk[role]["version"])
        validator("ModelPackageManifest").validate(m)
        assert m["role"] == role and m["adapter_hash"] == r.rec["stages"]["train"][role]["adapter_hash"]
        assert m["lineage"]["dataset"] == r.dataset_ref and m["lineage"]["training_run_id"] == r.run_id
        assert {q["id"] for q in m["lineage"]["question_sets"]} == {"qs-train"}      # 학습에 쓴 문항 세트만
        assert m["serving_config"] == cfg["serving"][role]
        assert r.registry.get_eval_report(m["eval_report_ref"])
    # 실행 기록: 설정, 시드, 데이터셋 버전, 라이브러리 버전
    assert rec["seed"] == 1234 and rec["dataset"]["version"] == "v1" and {"model-runtime", "boundary-contracts"} <= set(rec["libraries"]) and "turn-engine" not in rec["libraries"]
    assert rec["config"]["run"]["seed"] == 1234


def test_same_config_and_seed_give_same_hashes(cfg, tmp_path):
    a, b = mk(with_dirs(cfg, tmp_path, "a")), mk(with_dirs(cfg, tmp_path, "b"))
    ra, rb = a.run(), b.run()
    assert a.run_id == b.run_id
    for role in ("LORA_A", "LORA_B"):
        assert ra["stages"]["train"][role]["adapter_hash"] == rb["stages"]["train"][role]["adapter_hash"]
        assert ra["stages"]["evaluate"][role]["report_hash"] == rb["stages"]["evaluate"][role]["report_hash"]
        assert ra["result"]["packages"][role]["content_hash"] == rb["result"]["packages"][role]["content_hash"]


def test_different_seed_changes_run_and_adapter(cfg, tmp_path):
    c2 = with_dirs(cfg, tmp_path, "s2"); c2["run"]["seed"] = 99
    a, b = mk(cfg), mk(c2)
    a.run(); b.run()
    assert a.run_id != b.run_id
    assert a.rec["stages"]["train"]["LORA_A"]["adapter_hash"] != b.rec["stages"]["train"]["LORA_A"]["adapter_hash"]


def test_resume_from_checkpoint_gives_same_adapter(cfg, tmp_path):
    ref = mk(with_dirs(cfg, tmp_path, "ref")); ref.run()
    c = with_dirs(cfg, tmp_path, "int")
    r1 = mk(c, trainer_factory=lambda k: DryRunTrainer(interrupt_at_step=15))
    with pytest.raises(Interrupted):
        r1.run()
    assert r1.rec["stages"]["train"]["LORA_A"]["status"] == "running"
    assert (r1.dir / "work" / "LORA_A" / "ckpt.json").exists()
    r2 = mk(c)                                       # 같은 설정으로 다시 실행하면 체크포인트부터 이어 간다
    rec = r2.run()
    assert rec["stages"]["train"]["LORA_A"]["result"]["resumed_from_step"] == 10
    for role in ("LORA_A", "LORA_B"):
        assert rec["stages"]["train"][role]["adapter_hash"] == ref.rec["stages"]["train"][role]["adapter_hash"]
    assert rec["result"]["status"] == "complete"


def test_stop_after_then_continue_skips_done_stages(cfg):
    r = mk(cfg)
    r.run(stop_after="train")
    assert r.rec["result"] is None and r.rec["stages"]["train"]["status"] == "done"
    h = r.rec["stages"]["train"]["LORA_A"]["adapter_hash"]
    calls = []
    r2 = mk(cfg, trainer_factory=lambda k: calls.append(k) or DryRunTrainer())
    r2.run()
    assert calls == [] and r2.rec["stages"]["train"]["LORA_A"]["adapter_hash"] == h     # 학습을 다시 하지 않는다


def test_run_once_is_idempotent_and_follows_new_dataset_version(cfg, tmp_path, fixture_store):
    c = copy.deepcopy(cfg)
    root = tmp_path / "ds"
    shutil.copytree(fixture_store, root)
    c["inputs"]["dataset_store"]["args"]["root"] = str(root)
    log = lambda s: None
    first = run_once(c, clock=FIXED, log=log)
    again = run_once(c, clock=FIXED, log=log)
    assert first["action"] == "ran" and again == {"action": "skip", "run_id": first["run_id"], "dataset_version": "v1"}
    from tests.doubles.fixtures import build_dataset
    build_dataset(root, "fx-golden", "v2", max_cases=120)       # 새 데이터셋 버전이 생겼다
    third = run_once(c, clock=FIXED, log=log)
    assert third["action"] == "ran" and third["dataset_version"] == "v2" and third["run_id"] != first["run_id"]
    assert run_once(c, clock=FIXED, log=log)["action"] == "skip"


def test_publish_is_idempotent_across_reruns(cfg, tmp_path):
    r = mk(cfg); r.run()
    r.rec["stages"].pop("package"); r.rec["stages"].pop("publish"); r.rec["result"] = None; r._save()
    r2 = mk(cfg); rec = r2.run()
    assert rec["result"]["status"] == "complete"      # 같은 내용이므로 CONFLICT가 아니다
    assert r2.registry.list_versions("lora-a-parser") == [rec["result"]["packages"]["LORA_A"]["version"]]


def test_training_data_never_contains_eval(cfg):
    r = mk(cfg); r.run(stop_after="transform")
    ev = {json.loads(x)["sample_id"] for x in (r.dir / "data/LORA_A/eval_samples.jsonl").read_text().splitlines()}
    tr = {json.loads(x)["sample_id"] for x in (r.dir / "data/LORA_A/train.jsonl").read_text().splitlines()}
    va = {json.loads(x)["sample_id"] for x in (r.dir / "data/LORA_A/val.jsonl").read_text().splitlines()}
    assert ev and tr and not (ev & (tr | va))


def test_leak_between_eval_and_train_stops_pipeline(cfg, tmp_path):
    from tests.doubles.fixtures import build_dataset
    root = tmp_path / "leak"
    build_dataset(root, "fx-golden", "v1")
    p = root / "fx-golden" / "v1"
    m = json.loads((p / "manifest.json").read_text())
    trf = next(f["path"] for f in m["files"] if f["role"] == "LORA_A" and f["split"] == "TRAIN")
    evf = next(f["path"] for f in m["files"] if f["role"] == "LORA_A" and f["split"] == "EVAL")
    tr = [json.loads(x) for x in (p / trf).read_text().splitlines()]
    ev_lines = (p / evf).read_text().splitlines()
    e0 = json.loads(ev_lines[0]); e0["scenario_id"] = tr[0]["scenario_id"]       # 시나리오를 공유하게 만든다
    ev_lines[0] = json.dumps(e0, ensure_ascii=False, sort_keys=True)
    (p / evf).write_text("\n".join(ev_lines) + "\n")
    for f in m["files"]:
        if f["path"] == evf:
            f["content_hash"] = canonical.file_hash(p / evf)
    m["content_hash"] = canonical.content_hash(m)
    (p / "manifest.json").write_text(json.dumps(m))
    c = copy.deepcopy(cfg); c["inputs"]["dataset_store"]["args"]["root"] = str(root)
    with pytest.raises(PipelineError, match="공유"):
        mk(c).run()


def test_corrupt_dataset_file_is_integrity_error(cfg, tmp_path, fixture_store):
    root = tmp_path / "bad"; shutil.copytree(fixture_store, root)
    trf = next(f["path"] for f in json.loads((root / "fx-golden/v1/manifest.json").read_text())["files"] if f["role"] == "LORA_A" and f["split"] == "TRAIN")
    with open(root / "fx-golden/v1" / trf, "ab") as f:
        f.write(b"\n")
    c = copy.deepcopy(cfg); c["inputs"]["dataset_store"]["args"]["root"] = str(root)
    with pytest.raises(BoundaryError) as e:
        mk(c).run()
    assert e.value.code == "INTEGRITY_ERROR"


def test_missing_dataset_is_boundary_error(cfg):
    c = copy.deepcopy(cfg); c["inputs"]["dataset"] = {"id": "nope"}
    with pytest.raises(BoundaryError) as e:
        mk(c)
    assert e.value.code == "NOT_FOUND"


def test_report_structure_and_unseen_reported_separately(cfg):
    r = mk(cfg); r.run()
    rep = json.loads((r.dir / "eval/report-LORA_A.json").read_text())
    assert rep["unseen_question_sets"]["question_sets"] == ["qs-unseen@1"]
    assert rep["unseen_question_sets"]["aggregate"]["n"] > 0 and rep["seen_question_sets"]["n"] > 0
    assert rep["overall"]["n"] == rep["seen_question_sets"]["n"] + rep["unseen_question_sets"]["aggregate"]["n"]
    assert {"action", "reason", "question_id", "seen", "question_set"} <= set(rep["slices"])
    assert any(f["unseen"] for f in rep["eval_files"]) and any(not f["unseen"] for f in rep["eval_files"])
    repb = json.loads((r.dir / "eval/report-LORA_B.json").read_text())
    assert {"action", "reason", "next_type", "question_set", "seen"} <= set(repb["slices"])


def test_report_has_no_thresholds(cfg):
    r = mk(cfg); r.run()
    def keys(o, acc):
        if isinstance(o, dict):
            for k, v in o.items():
                acc.add(k); keys(v, acc)
        elif isinstance(o, list):
            for v in o: keys(v, acc)
        return acc
    for role in ("LORA_A", "LORA_B"):
        ks = keys(json.loads((r.dir / f"eval/report-{role}.json").read_text()), set())
        assert not [k for k in ks if "threshold" in k.lower() or k.lower() in ("pass", "passed", "verdict")]


def test_corrupt_outputs_go_through_protection_and_are_reported(cfg):
    c = copy.deepcopy(cfg); c["evaluation"]["backend"]["corrupt_every"] = 4
    r = mk(c); r.run()
    rep = json.loads((r.dir / "eval/report-LORA_A.json").read_text())
    o = rep["overall"]
    # LoRA-A는 보호가 없다(D75): 깨진 출력은 형식 실패(invalid)로 남고 대체 출력은 없다
    assert o["raw_valid"] < o["n"] and o["outcomes"].get("invalid", 0) > 0 and "fallback" not in o["outcomes"] and o["attempts"].get("3", 0) > 0
    assert o["rates"]["schema_ok_rate"] < 1.0 and o["protections"] == {}
    assert rep["failures"]["count"] > 0
    repb = json.loads((r.dir / "eval/report-LORA_B.json").read_text())
    assert repb["overall"]["fallback"] > 0                 # is_fallback은 코드가 채운다


def test_config_rejects_unknown_keys_and_thresholds(cfg):
    c = copy.deepcopy({k: v for k, v in cfg.items() if k != "_base_dir"})
    c["evaluation"]["threshold"] = 0.9
    with pytest.raises(ConfigError):
        validate_config(c)
    c = copy.deepcopy({k: v for k, v in cfg.items() if k != "_base_dir"})
    c["training"]["pass_rate"] = 0.9
    with pytest.raises(ConfigError):
        validate_config(c)


def test_config_hash_ignores_locations_but_not_training(cfg, tmp_path):
    from model_lab.config import config_hash
    c2 = with_dirs(cfg, tmp_path, "x")
    assert config_hash(cfg) == config_hash(c2)
    c3 = copy.deepcopy(cfg); c3["training"]["epochs"] = 2
    assert config_hash(cfg) != config_hash(c3)


def test_rank_check_before_publish(cfg):
    c = copy.deepcopy(cfg); c["checks"] = {"max_lora_rank": 16}      # dry-run 어댑터에는 순위가 없다 -> 확인 못 하면 중단
    with pytest.raises(PipelineError, match="max_lora_rank"):
        mk(c).run()


def test_eval_requires_temperature_zero(cfg):
    c = copy.deepcopy(cfg)
    c["serving"]["LORA_A"] = copy.deepcopy(c["serving"]["LORA_A"]); c["serving"]["LORA_A"]["decoding"]["temperature"] = 0.5
    with pytest.raises(PipelineError, match="온도 0"):
        mk(c).run()


def test_publish_to_remote_registry_over_http(cfg, tmp_path, monkeypatch):
    """registry.kind=http: 실제 boundary_contracts.clients.HttpModelRegistry + 환경 변수 토큰으로 우리 서버에 게시한다."""
    import threading
    from model_lab.service.app import Service, make_server
    svc = Service(tmp_path / "remote", {"r": "release-gate"}, {"w": "model-lab"}, log=lambda s: None)
    from tests.doubles.serve import serve
    srv, port = serve(svc)
    try:
        monkeypatch.setenv("TEST_REGISTRY_TOKEN", "w")
        c = copy.deepcopy(cfg)
        c["registry"] = {"kind": "http", "base_url": f"http://127.0.0.1:{port}", "token_env": "TEST_REGISTRY_TOKEN"}
        r = mk(c); rec = r.run()
        v = rec["result"]["packages"]["LORA_A"]["version"]
        assert svc.registry.get_manifest("lora-a-parser", v)["content_hash"] == rec["result"]["packages"]["LORA_A"]["content_hash"]
        assert svc.registry.list_ids()[0] == ["lora-a-parser", "lora-b-generator"]
        assert run_once(c, clock=FIXED, log=lambda s: None)["action"] == "skip"
    finally:
        stop(srv)


class FlakyRegistry:
    """첫 n번의 publish에서 UNAVAILABLE을 낸다."""
    def __init__(self, inner, fail_times):
        self.inner, self.left = inner, fail_times
    def publish(self, *a, **k):
        if self.left > 0:
            self.left -= 1
            raise BoundaryError("UNAVAILABLE", "시험: 레지스트리 장애")
        return self.inner.publish(*a, **k)


def test_registry_outage_keeps_training_result_and_retries_only_publish(cfg, tmp_path):
    from model_lab.registry import MemoryModelRegistry
    inner = MemoryModelRegistry()
    r1 = mk(cfg, registry=FlakyRegistry(inner, 1))
    with pytest.raises(BoundaryError) as e:
        r1.run()
    assert e.value.code == "UNAVAILABLE" and e.value.retryable
    assert r1.rec["stages"]["train"]["status"] == "done" and r1.rec["stages"]["package"]["status"] == "done"   # 학습·패키징 결과가 남는다
    assert r1.rec["result"] is None and inner.list_ids() == ([], None)
    h = r1.rec["stages"]["train"]["LORA_A"]["adapter_hash"]
    def no_train(kind):
        raise AssertionError("재학습하면 안 된다")
    r2 = mk(cfg, registry=inner, trainer_factory=no_train)
    rec = r2.run()
    assert rec["result"]["status"] == "complete" and rec["stages"]["train"]["LORA_A"]["adapter_hash"] == h
    assert inner.list_ids()[0] == ["lora-a-parser", "lora-b-generator"]


def test_run_once_exit_code_3_when_remote_unreachable(cfg, tmp_path, monkeypatch, capsys):
    import yaml
    from model_lab.cli import main
    monkeypatch.setenv("TEST_REGISTRY_TOKEN", "w")
    raw = {k: v for k, v in cfg.items() if k != "_base_dir"}
    raw["registry"] = {"kind": "http", "base_url": "http://127.0.0.1:9", "token_env": "TEST_REGISTRY_TOKEN", "timeout": 2}
    p = tmp_path / "remote.yaml"; p.write_text(yaml.safe_dump(raw))
    assert main(["run-once", "--config", str(p)]) == 3                  # 재시도 가능
    assert main(["run-once", "--config", str(p)]) == 3                  # 학습은 다시 하지 않고 게시만 재시도(아래 확인)
    out = capsys.readouterr().out
    assert out.count("학습 시작") == 2                                    # LORA_A, LORA_B가 첫 실행에서 한 번씩만


def test_package_records_resume_and_evaluation_backend(cfg):
    c = copy.deepcopy(cfg)
    r1 = mk(c, trainer_factory=lambda k: DryRunTrainer(interrupt_at_step=15))
    with pytest.raises(Interrupted):
        r1.run()
    r2 = mk(c); rec = r2.run()
    m = r2.registry.get_manifest("lora-a-parser", rec["result"]["packages"]["LORA_A"]["version"])
    assert m["ext"]["model-lab"]["resumed"] is True and m["ext"]["model-lab"]["resumed_from_step"] == 10
    assert m["base_model"]["id"] == "Qwen/Qwen2.5-1.5B-Instruct"            # '/' 그대로(계약 v1.2.0)
    rep = json.loads(r2.registry.get_eval_report(m["eval_report_ref"]))
    assert rep["evaluation_backend"]["name"] == "fake" and rep["evaluation_backend"]["constrained_decoding"] is False


def test_http_kind_requires_token_env_var(cfg):
    c = copy.deepcopy(cfg); c["registry"] = {"kind": "http", "base_url": "http://x", "token_env": "NOPE_TOKEN_VAR"}
    with pytest.raises(ConfigError):
        mk(c)


def test_env_args_resolution():
    from model_lab.factories import resolve_args
    assert resolve_args({"base_url": "u", "token_env": "T"}, {"T": "s"}) == {"base_url": "u", "token": "s"}
    with pytest.raises(ConfigError):
        resolve_args({"token_env": "MISSING"}, {})


def test_neighbor_reader_factories_are_accepted_by_config(cfg):
    c = copy.deepcopy({k: v for k, v in cfg.items() if k != "_base_dir"})
    c["inputs"]["question_reader"] = {"factory": "boundary_contracts.clients:HttpQuestionReader", "args": {"base_url": "http://q", "token_env": "QT"}}
    c["inputs"]["failure_store"] = {"factory": "boundary_contracts.clients:HttpFailureStore", "args": {"base_url": "http://f", "token_env": "FT"}}
    validate_config(c)


def test_all_shipped_configs_are_valid():
    from pathlib import Path
    from model_lab.config import load_config
    files = sorted(f for f in (Path(__file__).resolve().parents[1] / "configs").glob("*.yaml") if not f.name.startswith("train"))  # train.yaml은 학습 감싸기 전용 형식(test_train_wrap)
    assert len(files) >= 7
    for f in files:
        load_config(f)
