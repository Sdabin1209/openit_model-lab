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
if ":" in a[-1]:              # 올리기(로컬 -> 서버)
    dst = a[-1].split(":", 1)[1]
    dst = os.path.join(os.environ["FAKE_HOME"], dst[2:]) if dst.startswith("~/") else dst
    shutil.copytree(a[-2], dst, dirs_exist_ok=True)
    sys.exit(0)
src, dst = a[-2].split(":", 1)[1], a[-1]
if src.startswith("~/"):      # 원격 셸이 ~를 서버 홈으로 푸는 것을 흉내 낸다
    src = os.path.join(os.environ["FAKE_HOME"], src[2:])
elif not src.startswith("/"):
    src = os.path.join(os.environ["FAKE_HOME"], src)
shutil.copytree(src, dst, dirs_exist_ok=True)
'''
FAKE_SMI = ('#!/bin/sh\n[ -n "$FAKE_NO_GPU" ] && exit 9\n'
            'case "$1" in --query-compute-apps*) [ -n "$FAKE_GPU_BUSY" ] && echo "4242, python, 9000 MiB"; exit 0;; esac\n'
            'echo "GPU 0: Fake RTX (UUID: x)"\n')


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
                         script_sha256={"LORA_A": sha}, python="python3", require_gpu=True, python_modules=["json"], sandbox_root="~/dabin/openit-model-lab",
                         pass_env=["FAKE_LOG", "FAKE_HOME", "FAKE_NO_GPU", "FAKE_GPU_BUSY", "FAKE_DOWN"])   # R9.1: 가짜 서버가 읽는 이름만 명시
    raw["server"]["arg_map"] = {k: None for k in raw["server"]["arg_map"]}
    raw["server"]["arg_map"].update(base_model="--model", dataset_dir="--data_dir", dataset_prefix="--prefix", output_dir="--out",
                                    learning_rate="--lr", epochs="--epochs")
    raw["base_model"].update(id="Qwen/Qwen3-4B", profile="qwen3-4b")
    raw["dataset"].update(dir=str(ds), prefix="lora_a")
    raw["output"].update(expected_gb={"upload": 0.1, "output": 1, "model_download": 1}, adapter_dir=str(tmp_path / "local" / "adapter"), server_out_dir="~/dabin/openit-model-lab/runs/t1", min_free_gb=0)
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
    train = [c for c in calls() if c["tool"] == "ssh" and "exec env TMPDIR" in c["argv"][-1]]
    assert len(train) == 1
    a = train[0]["argv"]
    assert a[:-2] == ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=60"] and a[-2] == "openit"
    SB = '"$HOME"/dabin/openit-model-lab'
    assert a[-1] == (f"mkdir -p {SB}/code {SB}/tmp {SB}/mpl && cp -f -- \"$HOME\"/taehyeon/fake_server_train.py {SB}/code/fake_server_train.py "
                     f"&& cd {SB}/code && echo '{sha}  fake_server_train.py' | sha256sum -c --quiet - && exec env TMPDIR={SB}/tmp MPLCONFIGDIR={SB}/mpl "
                     f"python3 fake_server_train.py --model Qwen/Qwen3-4B --data_dir {raw['dataset']['dir']} --prefix lora_a --out {SB}/runs/t1 --epochs 3 --lr 0.0002")
    rs = [c["argv"] for c in calls() if c["tool"] == "rsync"]
    assert len(rs) == 1 and rs[0][-2] == "openit:~/dabin/openit-model-lab/runs/t1/adapter/" and rs[0][-1].endswith("/local/adapter/")
    assert (home / "dabin" / "openit-model-lab" / "runs" / "t1" / "adapter" / "adapter_model.safetensors").is_file()   # 서버에서 만들어짐
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
    assert not (home / "dabin").exists()


def test_script_changed_between_check_and_run_is_stopped_on_the_server(srv):
    raw, write, calls, home, sha = srv
    cfg = tw.load_train_config(write())
    cmd = tw.plan(cfg)["command"]
    (home / "taehyeon" / "fake_server_train.py").write_text("print('바뀐 코드')\n")        # check_ready 뒤에 서버 코드가 바뀐 경우(복사 때 바뀐 것을 사본이 가져온다)
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode != 0 and "바뀐 코드" not in r.stdout and not (home / "dabin" / "openit-model-lab" / "runs").exists()


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
    assert "sha256sum -c" in out and "openit:~/dabin/openit-model-lab/runs/t1/adapter/" in out and "실행하지 않았다" in out
    assert not [c for c in calls()[before:] if "exec" in c["argv"][-1]]


# ---- 서버 쓰기는 ~/dabin 아래뿐 (사용자 결정 2026-11-03) ----
def test_every_write_path_in_the_command_is_under_dabin_and_no_cache_overwrite_options(srv):
    import re
    raw, write, calls, home, sha = srv
    cmd = tw.plan(tw.load_train_config(write()))["command"][-1]
    # 쓰기 대상: mkdir 인자, cp의 도착지, cd, env 값, --out(출력). 모두 "$HOME"/dabin/ 아래
    writes = re.findall(r'mkdir -p ((?:"\$HOME"/\S+ ?)+)&&', cmd)[0].split() + re.findall(r'cp -f -- \S+ (\S+)', cmd) + \
        re.findall(r'cd (\S+)', cmd) + re.findall(r'(?:TMPDIR|MPLCONFIGDIR)=(\S+)', cmd) + re.findall(r'--out (\S+)', cmd)
    assert len(writes) == 3 + 1 + 1 + 2 + 1 and all(w.startswith('"$HOME"/dabin/openit-model-lab/') for w in writes), writes
    # 모델 캐시는 서버 기본 위치를 쓰므로 덮어쓰지 않는다: 위치를 바꾸는 변수도, 덮어쓰기·정리 옵션도 명령에 없다
    for forbidden in ("HF_HOME", "XDG_CACHE_HOME", "TRANSFORMERS_CACHE", "HF_HUB_CACHE", "force_download", "--force", "delete-cache", "rm ", "rmdir", "resume_download", "--revision"):
        assert forbidden not in cmd, forbidden
    assert tw.plan(tw.load_train_config(write()))["write_paths"]["output.server_out_dir"].startswith("~/dabin/")


@pytest.mark.parametrize("key,value", [("sandbox_root", "~/taehyeon/x"), ("sandbox_root", "~/dabin/../taehyeon"), ("sandbox_root", "/home/openit/dabin/x"),
                                       ("server_out_dir", "runs/t1"), ("server_out_dir", "~/taehyeon/runs/t1"), ("upload_root", "~/uploads")])
def test_paths_outside_dabin_fail_doctor_and_refuse_before_touching_the_server(srv, key, value):
    raw, write, calls, home, sha = srv
    if key == "sandbox_root":
        raw["server"]["sandbox_root"] = value
    elif key == "server_out_dir":
        raw["output"]["server_out_dir"] = value
    else:
        raw["dataset"]["upload_root"] = value
    rows = {n: (st, m) for st, n, m in tw.doctor(tw.load_train_config(write()))}
    assert rows["서버 쓰기 경로"][0] == "FAIL" and "~/dabin" in rows["서버 쓰기 경로"][1]
    n = len(calls())
    with pytest.raises(PipelineError, match="~/dabin 아래뿐"):
        tw.run_train(tw.load_train_config(write()), log=lambda m: None)
    assert not [c for c in calls()[n:] if "exec" in c["argv"][-1] or "mkdir" in c["argv"][-1]] and not [c for c in calls() if c["tool"] == "rsync"]
    assert not (home / "dabin").exists()


def test_ssh_without_sandbox_root_is_refused(srv):
    raw, write, calls, home, sha = srv
    del raw["server"]["sandbox_root"]
    assert {n: st for st, n, m in tw.doctor(tw.load_train_config(write()))}["서버 쓰기 경로"] == "FAIL"
    with pytest.raises(PipelineError, match="sandbox_root"):
        tw.run_train(tw.load_train_config(write()), log=lambda m: None)


def test_shipped_server_configs_keep_all_writes_under_dabin():
    for f in ("train.yaml", "train-b.yaml"):
        cfg = tw.load_train_config(ROOT / "configs" / f)
        from model_lab.components import server_train_wrap as w
        assert w.sandbox_violations(cfg) == [] and "dataset.upload_root" in w.sandbox_paths(cfg), f
        assert "HF_HOME" not in tw.plan(cfg)["command"][-1]


# ---- 공유 서버: 남의 작업을 훼손하지 않는다 ----
def test_gpu_in_use_by_someone_else_blocks_run_exit_2_and_doctor_reports(srv, monkeypatch, capsys):
    raw, write, calls, home, sha = srv
    monkeypatch.setenv("FAKE_GPU_BUSY", "1")
    rows = {n: (st, m) for st, n, m in tw.doctor(tw.load_train_config(write()))}
    assert rows["GPU 점유"][0] == "FAIL" and "다른 프로세스" in rows["GPU 점유"][1] and "중단하지 않는다" in rows["GPU 점유"][1]
    assert cli_main(["train", "--config", str(write())]) == 2
    assert "다른 프로세스" in capsys.readouterr().err
    assert not [c for c in calls() if "exec" in c["argv"][-1] or "mkdir" in c["argv"][-1]] and not (home / "dabin").exists()   # 실행도, 서버 쓰기도 없음
    monkeypatch.delenv("FAKE_GPU_BUSY")
    assert {n: st for st, n, m in tw.doctor(tw.load_train_config(write()))}["GPU 점유"] == "OK"


def test_low_server_disk_refuses_and_doctor_fails(srv, capsys):
    raw, write, calls, home, sha = srv
    raw["output"]["expected_gb"] = {"upload": 1e9, "output": 1e9, "model_download": 1e9}      # 어떤 디스크도 모자란 크기
    rows = {n: (st, m) for st, n, m in tw.doctor(tw.load_train_config(write()))}
    assert rows["서버 디스크"][0] == "FAIL" and "필요" in rows["서버 디스크"][1] and "부족" in rows["서버 디스크"][1]
    assert cli_main(["train", "--config", str(write())]) == 2
    assert not [c for c in calls() if "exec" in c["argv"][-1]] and not (home / "dabin").exists()
    del raw["output"]["expected_gb"]
    assert {n: st for st, n, m in tw.doctor(tw.load_train_config(write()))}["서버 디스크"] == "FAIL"     # 예상 크기가 없으면 확인할 수 없어 거부
    raw["output"]["expected_gb"] = {"upload": 0.1, "output": 1, "model_download": 1}
    assert {n: st for st, n, m in tw.doctor(tw.load_train_config(write()))}["서버 디스크"] == "OK"


# ---- RUNTIME R9.1: 상속받은 환경을 통째로 넘기지 않는다 ----
def test_child_processes_get_only_the_allowed_environment(srv, tmp_path, monkeypatch):
    raw, write, calls, home, sha = srv
    dump = tmp_path / "env-seen.json"
    spy = tmp_path / "bin" / "ssh"
    spy.write_text(f"#!{sys.executable}\nimport json, os\njson.dump(sorted(os.environ), open({str(dump)!r}, 'w'))\n")
    spy.chmod(0o755)
    monkeypatch.setenv("SOME_SECRET_VALUE", "s3cr3t")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.jsonl"))
    monkeypatch.setenv("UNLISTED_VAR", "x")
    from model_lab.components import server_train_wrap as w
    w.ssh_run(tw.load_train_config(write()), "true")
    seen = set(json.loads(dump.read_text()))
    assert "PATH" in seen and "FAKE_LOG" in seen                      # 허용 목록(기본 + server.pass_env)
    assert "SOME_SECRET_VALUE" not in seen and "UNLISTED_VAR" not in seen
