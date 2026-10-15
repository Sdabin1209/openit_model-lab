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
    assert rec["seed"] == 1234 and rec["dataset"]["version"] == "v1" and {"turn-engine", "model-runtime", "boundary-contracts"} <= set(rec["libraries"])
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
    tr = [json.loads(x) for x in (p / "parser-train.jsonl").read_text().splitlines()]
    ev_lines = (p / "parser-eval.jsonl").read_text().splitlines()
    e0 = json.loads(ev_lines[0]); e0["scenario_id"] = tr[0]["scenario_id"]       # 시나리오를 공유하게 만든다
    ev_lines[0] = json.dumps(e0, ensure_ascii=False, sort_keys=True)
    (p / "parser-eval.jsonl").write_text("\n".join(ev_lines) + "\n")
    m = json.loads((p / "manifest.json").read_text())
    for f in m["files"]:
        if f["path"] == "parser-eval.jsonl":
            f["content_hash"] = canonical.file_hash(p / "parser-eval.jsonl")
    m["content_hash"] = canonical.content_hash(m)
    (p / "manifest.json").write_text(json.dumps(m))
    c = copy.deepcopy(cfg); c["inputs"]["dataset_store"]["args"]["root"] = str(root)
    with pytest.raises(PipelineError, match="공유"):
        mk(c).run()


def test_corrupt_dataset_file_is_integrity_error(cfg, tmp_path, fixture_store):
    root = tmp_path / "bad"; shutil.copytree(fixture_store, root)
    with open(root / "fx-golden/v1/parser-train.jsonl", "ab") as f:
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
    assert {"slot_kind", "unit", "range", "question_id", "intent", "seen", "question_set"} <= set(rep["slices"])
    assert rep["eval_files"][0]["unseen"] is True
    repb = json.loads((r.dir / "eval/report-LORA_B.json").read_text())
    assert {"action", "question_set", "seen"} <= set(repb["slices"])


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
    assert o["raw_valid"] < o["n"] and o["outcomes"].get("fallback", 0) > 0 and o["attempts"].get("3", 0) > 0
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
