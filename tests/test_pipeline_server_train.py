"""파이프라인(run)의 train 단계가 서버 정본 실행 경로를 부르는지 (G06, ADR 0031). 가짜 ssh/rsync(tests/test_train_ssh.py)로 서버를 대신한다.
진짜 서버·진짜 학습은 시험하지 않는다 [구멍: GPU·서버]."""
import copy
import json

import pytest
import yaml

from model_lab.components import server_row_convert
from model_lab.wiring import schemas
from model_lab.wiring.errors import ConfigError
from model_lab.wiring.pipeline import Run, make_trainer
from tests.conftest import FIXED
from tests.test_train_ssh import srv  # noqa: F401  (fixture)

# 학습 서버 데이터의 한 줄이 가진 키(2026-11-03 ssh openit으로 lora_a_train.jsonl 첫 줄을 읽음): human_review(선택), messages, scenario_id, split
SERVER_ROW_KEYS = {"human_review", "messages", "scenario_id", "split"}


def server_cfgs(srv, cfg, tmp_path):
    raw, write, calls, home, sha = srv
    paths = {}
    for role, pre in (("LORA_A", "lora_a"), ("LORA_B", "lora_b")):
        r = copy.deepcopy(raw)
        r["role"] = role
        r["server"]["script_sha256"] = {role: sha}
        r["dataset"].update(prefix=pre, upload_root="~/dabin/openit-model-lab/uploads")
        r["output"].update(adapter_dir=str(tmp_path / "unused" / role), server_out_dir="~/dabin/openit-model-lab/runs/pl")
        p = tmp_path / f"train-{pre}.yaml"; p.write_text(yaml.safe_dump(r)); paths[role] = str(p)
    c = copy.deepcopy(cfg)
    c["training"] = {"trainer": "server"}
    for role in c["roles"]:
        c["roles"][role]["training"] = {"server_train_config": paths[role]}
    return c


def test_run_train_stage_uploads_server_format_data_and_runs_pinned_script(srv, cfg, tmp_path):
    raw, write, calls, home, sha = srv
    c = server_cfgs(srv, cfg, tmp_path)
    r = Run(c, clock=FIXED, log=lambda s: None)
    r.run(stop_after="train")
    # transform 출력 = 서버 스크립트가 읽는 형식(서버 데이터 한 줄과 같은 키 구조)
    for role, pre in (("LORA_A", "lora_a"), ("LORA_B", "lora_b")):
        d = home / "dabin" / "openit-model-lab" / "uploads" / r.run_id / role
        assert sorted(x.name for x in d.iterdir()) == [f"{pre}_test.jsonl", f"{pre}_train.jsonl", f"{pre}_val.jsonl"]
        rows = [json.loads(x) for x in (d / f"{pre}_train.jsonl").read_text(encoding="utf-8").splitlines()]
        assert rows
        for row in rows:
            assert {"messages", "scenario_id", "split"} <= set(row) <= SERVER_ROW_KEYS and row["split"] == "train"
            assert [m["role"] for m in row["messages"]] == ["system", "user", "assistant"] and not schemas.errors("ServerDatasetRow", row)
            sample, sysp = server_row_convert.from_server_row(role, row)          # 서버 형식으로 다시 읽으면 계약 샘플이 된다
            assert sysp == row["messages"][0]["content"]
        assert not (r.dir / "data" / role / "train.jsonl").exists()                  # 자체 학습기용 prompt/completion 파일은 만들지 않는다
    # train 단계: 서버 정본 실행 명령(해시 확인 + exec)과 어댑터 회수
    ssh = [c_["argv"][-1] for c_ in calls() if c_["tool"] == "ssh"]
    execs = [x for x in ssh if "exec env TMPDIR" in x]
    assert len(execs) == 2 and all("sha256sum -c --quiet -" in x for x in execs)
    for role, pre in (("LORA_A", "lora_a"), ("LORA_B", "lora_b")):   # 데이터는 서버 업로드 폴더 아래 <run_id>/<role>, 출력은 run_id로 나뉜 새 폴더
        assert any(f'--data_dir "$HOME"/dabin/openit-model-lab/uploads/{r.run_id}/{role} --prefix {pre} --out "$HOME"/dabin/openit-model-lab/runs/pl/{r.run_id}' in x for x in execs), execs
    assert any("mkdir -p" in x and f"dabin/openit-model-lab/uploads/{r.run_id}/LORA_A" in x for x in ssh)
    assert [c_ for c_ in calls() if c_["tool"] == "rsync"]
    for role in ("LORA_A", "LORA_B"):
        assert (r.dir / "adapters" / role / "adapter_model.safetensors").is_file()
        assert r.rec["stages"]["train"][role]["trainer"] == "server" and r.rec["stages"]["train"][role]["status"] == "done"


def test_hf_trainer_is_retired_in_pipeline():
    with pytest.raises(ConfigError, match="폐기"):
        make_trainer("hf")


def test_server_trainer_needs_train_config_and_upload_root(srv, cfg, tmp_path):
    c = server_cfgs(srv, cfg, tmp_path)
    del c["roles"]["LORA_A"]["training"]
    with pytest.raises(Exception, match="server_train_config"):
        Run(c, clock=FIXED, log=lambda s: None).run(stop_after="train")
