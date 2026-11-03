"""`model-lab train`의 원격 실행(server.ssh, G06). 가짜 ssh/rsync/nvidia-smi가 PATH에 있어 받은 명령을 기록하고,
원격 명령은 이 컴퓨터의 bash가 가짜 서버 홈(HOME=fake_home)에서 대신 실행한다. 진짜 서버 접속·진짜 학습은 시험하지 않는다 [구멍: GPU·서버]."""
import hashlib
import json
import os
import subprocess
import sys

import pytest
import yaml

from model_lab.cli import main as cli_main
from model_lab.wiring import train_flow as tw
from model_lab.wiring.errors import PipelineError
from tests.conftest import ROOT

STUB = (ROOT / "tests" / "doubles" / "fake_server_train.py").read_text()

FAKE_SSH = f'''#!{sys.executable}
import json, os, subprocess, sys
a = sys.argv[1:]
open(os.environ["FAKE_LOG"], "a").write(json.dumps({{"tool": "ssh", "argv": a}}, ensure_ascii=False) + "\\n")
if os.environ.get("FAKE_DOWN"):
    sys.exit(255)
host, cmd = a[-2], a[-1]
sys.exit(subprocess.run(["bash", "-c", cmd], env={{**os.environ, "HOME": os.environ["FAKE_HOME"]}}).returncode)
'''
FAKE_RSYNC = f'''#!{sys.executable}
import json, os, shutil, sys
a = sys.argv[1:]
open(os.environ["FAKE_LOG"], "a").write(json.dumps({{"tool": "rsync", "argv": a}}, ensure_ascii=False) + "\\n")
src, dst = a[-2].split(":", 1)[1], a[-1]
if src.startswith("~/"):      # 원격 셸이 ~를 서버 홈으로 푸는 것을 흉내 낸다
    src = os.path.join(os.environ["FAKE_HOME"], src[2:])
elif not src.startswith("/"):
    src = os.path.join(os.environ["FAKE_HOME"], src)
shutil.copytree(src, dst, dirs_exist_ok=True)
'''
FAKE_SMI = '#!/bin/sh\n[ -n "$FAKE_NO_GPU" ] && exit 9\necho "GPU 0: Fake RTX (UUID: x)"\n'


@pytest.fixture
def srv(tmp_path, monkeypatch):
    bin_, home, log = tmp_path / "bin", tmp_path / "fake_home", tmp_path / "calls.jsonl"
    bin_.mkdir(); (home / "taehyeon").mkdir(parents=True)
    for n, body in (("ssh", FAKE_SSH), ("rsync", FAKE_RSYNC), ("nvidia-smi", FAKE_SMI)):
        (bin_ / n).write_text(body); (bin_ / n).chmod(0o755)
    script = STUB.replace("out = Path(a.out);", 'out = Path(a.out) / "adapter";')   # 서버 규칙: 어댑터는 <out_dir>/adapter/
    (home / "taehyeon" / "fake_server_train.py").write_text(script)
    sha = hashlib.sha256(script.encode()).hexdigest()
    ds = tmp_path / "ds"; ds.mkdir()
    for s in tw.SPLITS:
        (ds / f"lora_a_{s}.jsonl").write_text('{"messages": []}\n')
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log)); monkeypatch.setenv("FAKE_HOME", str(home))
    raw = yaml.safe_load((ROOT / "configs" / "train.yaml").read_text())
    raw["server"].update(code_dir="~/taehyeon", scripts={"LORA_A": "fake_server_train.py", "LORA_B": "fake_server_train.py"},
                         script_sha256={"LORA_A": sha}, python="python3", require_gpu=True, python_modules=["json"])
    raw["server"]["arg_map"] = {k: None for k in raw["server"]["arg_map"]}
    raw["server"]["arg_map"].update(base_model="--model", dataset_dir="--data_dir", dataset_prefix="--prefix", output_dir="--out",
                                    learning_rate="--lr", epochs="--epochs")
    raw["base_model"].update(id="Qwen/Qwen3-4B", profile="qwen3-4b")
    raw["dataset"].update(dir=str(ds), prefix="lora_a")
    raw["output"].update(adapter_dir=str(tmp_path / "local" / "adapter"), server_out_dir="runs/t1", min_free_gb=0)
    path = tmp_path / "train.yaml"

    def write(r=raw):
        path.write_text(yaml.safe_dump(r)); return path
    write()

    def calls():
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    return raw, write, calls, home, sha


def test_train_runs_pinned_script_over_ssh_then_fetches_adapter(srv):
    raw, write, calls, home, sha = srv
    rec = tw.run_train(tw.load_train_config(write()), log=lambda m: None)
    train = [c for c in calls() if c["tool"] == "ssh" and "exec python3" in c["argv"][-1]]
    assert len(train) == 1
    a = train[0]["argv"]
    assert a[:-2] == ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=60"] and a[-2] == "openit"
    assert a[-1] == (f"cd \"$HOME\"/taehyeon && echo '{sha}  fake_server_train.py' | sha256sum -c --quiet - && exec python3 fake_server_train.py "
                     f"--model Qwen/Qwen3-4B --data_dir {raw['dataset']['dir']} --prefix lora_a --out runs/t1 --epochs 3 --lr 0.0002")
    rs = [c["argv"] for c in calls() if c["tool"] == "rsync"]
    assert len(rs) == 1 and rs[0][-2] == "openit:~/taehyeon/runs/t1/adapter/" and rs[0][-1].endswith("/local/adapter/")
    assert (home / "taehyeon" / "runs" / "t1" / "adapter" / "adapter_model.safetensors").is_file()   # 서버에서 만들어짐
    assert rec["server_script"] == "openit:~/taehyeon/fake_server_train.py" and rec["server_script_sha256"] == sha
    assert len(rec["dataset"]["file_sha256"]) == 3 and rec["adapter"]["r"] == 16
    from pathlib import Path
    assert (Path(raw["output"]["adapter_dir"]) / "adapter_config.json").is_file()                     # package-adapters 입력 위치


def test_sha_mismatch_is_refused_before_anything_runs(srv):
    raw, write, calls, home, sha = srv
    raw["server"]["script_sha256"]["LORA_A"] = "0" * 64
    with pytest.raises(PipelineError, match="서버 코드가 바뀌었다"):
        tw.run_train(tw.load_train_config(write()), log=lambda m: None)
    assert not [c for c in calls() if "exec" in c["argv"][-1]] and not [c for c in calls() if c["tool"] == "rsync"]
    assert not (home / "taehyeon" / "runs").exists()


def test_script_changed_between_check_and_run_is_stopped_on_the_server(srv):
    raw, write, calls, home, sha = srv
    cfg = tw.load_train_config(write())
    cmd = tw.plan(cfg)["command"]
    (home / "taehyeon" / "fake_server_train.py").write_text("print('바뀐 코드')\n")        # check_ready 뒤에 서버 코드가 바뀐 경우
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode != 0 and "바뀐 코드" not in r.stdout and not (home / "taehyeon" / "runs").exists()


def test_empty_sha_fails_doctor_with_how_to_fill_and_blocks_run(srv, capsys):
    raw, write, calls, home, sha = srv
    raw["server"]["script_sha256"] = {}
    assert cli_main(["train-doctor", "--config", str(write())]) == 1
    out = capsys.readouterr().out
    line = next(x for x in out.splitlines() if "서버 코드 해시" in x)
    assert line.startswith("[FAIL]") and sha in line and "ssh openit cat" in line and "ssh openit sha256sum" in line and "server.script_sha256.LORA_A" in line
    assert cli_main(["train", "--config", str(write())]) == 2
    assert not [c for c in calls() if "exec" in c["argv"][-1]]


def test_doctor_output_over_ssh_all_ok(srv, capsys):
    raw, write, calls, home, sha = srv
    assert cli_main(["train-doctor", "--config", str(write())]) == 0
    rows = {l.split("]", 1)[1].split(":", 1)[0].strip(): l.split("]")[0].strip("[ ") for l in capsys.readouterr().out.splitlines()}
    assert rows["서버 접속"] == "OK" and rows["서버 스크립트"] == "OK" and rows["데이터셋"] == "OK" and rows["GPU"] == "OK"
    assert rows["서버 코드 해시"] == "OK" and rows["서버 인자 호환"] == "OK" and rows["파이썬 의존"] == "OK"
    assert all(c["tool"] == "ssh" and c["argv"][-2] == "openit" for c in calls())                    # 서버에는 읽기·점검 명령만
    assert not [c for c in calls() if "exec" in c["argv"][-1]]


def test_doctor_fails_without_gpu_data_or_connection(srv, monkeypatch, capsys):
    raw, write, calls, home, sha = srv
    monkeypatch.setenv("FAKE_NO_GPU", "1")
    rows = {n: (st, m) for st, n, m in tw.doctor(tw.load_train_config(write()))}
    assert rows["GPU"][0] == "FAIL"
    with pytest.raises(PipelineError, match="GPU"):
        tw.run_train(tw.load_train_config(write()), log=lambda m: None)
    monkeypatch.delenv("FAKE_NO_GPU")
    raw["dataset"]["dir"] = "/nonexistent/ds"
    assert {n: st for st, n, m in tw.doctor(tw.load_train_config(write()))}["데이터셋"] == "FAIL"
    raw["dataset"]["dir"] = str(home)
    monkeypatch.setenv("FAKE_DOWN", "1")
    rows = {n: st for st, n, m in tw.doctor(tw.load_train_config(write()))}
    assert rows["서버 접속"] == "FAIL" and rows["GPU"] == "FAIL"
    assert cli_main(["train-doctor", "--config", str(write())]) == 1
    assert cli_main(["train", "--config", str(write())]) == 2


def test_dry_run_prints_ssh_command_and_touches_nothing_on_the_server(srv, capsys):
    raw, write, calls, home, sha = srv
    before = len(calls())
    assert cli_main(["train", "--config", str(write()), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "sha256sum -c" in out and "openit:~/taehyeon/runs/t1/adapter/" in out and "실행하지 않았다" in out
    assert not [c for c in calls()[before:] if "exec" in c["argv"][-1]]
