"""gpu-dev: 이 PC에서 서버 정본 스크립트를 로컬로 실행하는 길 (fetch-train-script, ssh 없는 학습 설정, ADR 0032). 스크립트는 대역이고 학습은 하지 않는다 [구멍: GPU]."""
import copy
import hashlib
import json

import pytest
import yaml

from model_lab.cli import main as cli_main
from model_lab.wiring import train_flow as tw
from model_lab.wiring.errors import PipelineError
from model_lab.wiring.pipeline import Run
from tests.conftest import FIXED, ROOT
from tests.test_train_ssh import srv  # noqa: F401  (fixture: 가짜 ssh/rsync/nvidia-smi + 가짜 서버 홈)
from tests.test_train_ssh import STUB

SERVER_SCRIPT = STUB.replace("out = Path(a.out);", 'out = Path(a.out) / "adapter";')


def test_shipped_gpu_dev_configs_agree_with_server_configs():
    for pre, src in (("a", "train.yaml"), ("b", "train-b.yaml")):
        g = tw.load_train_config(ROOT / "configs" / f"train-gpu-dev-{pre}.yaml")
        s = tw.load_train_config(ROOT / "configs" / src)
        assert "ssh" not in g["server"] and g["server"]["script_sha256"] == s["server"]["script_sha256"]    # 같은 정본 스크립트
        assert g["server"]["arg_map"] == s["server"]["arg_map"] and g["base_model"]["id"] == "Qwen/Qwen2.5-1.5B-Instruct"
        cmd = tw.plan(g)["command"]
        assert cmd[1].endswith(f"train-scripts/train_lora_{pre}.py") and cmd[cmd.index("--model") + 1] == "Qwen/Qwen2.5-1.5B-Instruct"
        assert cmd[cmd.index("--batch") + 1] == "1" and cmd[cmd.index("--accum") + 1] == "8"
    for f in (ROOT / "configs").glob("gpu-dev*.yaml"):                  # gpu-dev 설정은 모두 이 학습 설정을 가리킨다
        roles = yaml.safe_load(f.read_text())["roles"]
        assert {r: v["training"]["server_train_config"] for r, v in roles.items()} == {"LORA_A": "train-gpu-dev-a.yaml", "LORA_B": "train-gpu-dev-b.yaml"}, f


def test_fetch_train_script_saves_only_when_sha_matches(srv, tmp_path, capsys):
    raw, write, calls, home, sha = srv
    dest = tmp_path / "cache"
    assert cli_main(["fetch-train-script", "--config", str(write()), "--dest", str(dest)]) == 0
    saved = dest / "fake_server_train.py"
    assert hashlib.sha256(saved.read_bytes()).hexdigest() == sha and str(saved) in capsys.readouterr().out
    assert all(c["tool"] == "ssh" and c["argv"][-1].startswith("cat ") for c in calls())             # 서버에는 읽기만
    dest2 = tmp_path / "cache2"
    raw["server"]["script_sha256"]["LORA_A"] = "0" * 64
    assert cli_main(["fetch-train-script", "--config", str(write()), "--dest", str(dest2)]) == 2
    assert not dest2.exists()                                                                       # 불일치: 아무것도 쓰지 않는다
    raw["server"]["script_sha256"] = {}
    assert cli_main(["fetch-train-script", "--config", str(write()), "--dest", str(dest2)]) == 2 and not dest2.exists()
    raw["server"].pop("ssh")
    with pytest.raises(PipelineError, match="server.ssh"):
        tw_cfg = tw.load_train_config(write()); __import__("model_lab.components.server_train_wrap", fromlist=["x"]).fetch_script(tw_cfg, dest2)


def test_gpu_dev_pipeline_reaches_and_passes_train_stage_with_fake_local_script(srv, cfg, tmp_path):
    """실제 gpu-dev 학습 설정을 쓰되 정본 스크립트만 해시가 맞는 대역으로 바꾼다. ssh/rsync 호출이 없어야 한다."""
    raw, write, calls, home, sha = srv
    code = tmp_path / "train-scripts"; code.mkdir()
    (code / "fake_server_train.py").write_text(SERVER_SCRIPT)
    c = copy.deepcopy(cfg); c["training"] = {"trainer": "server"}
    for role, pre in (("LORA_A", "lora_a"), ("LORA_B", "lora_b")):
        g = yaml.safe_load((ROOT / "configs" / f"train-gpu-dev-{pre[-1]}.yaml").read_text())
        g["server"].update(code_dir=str(code), scripts={"LORA_A": "fake_server_train.py", "LORA_B": "fake_server_train.py"},
                           script_sha256={role: sha}, python="python3", python_modules=["json"], require_gpu=False)
        g["server"]["arg_map"] = {k: None for k in g["server"]["arg_map"]}
        g["server"]["arg_map"].update(base_model="--model", dataset_dir="--data_dir", dataset_prefix="--prefix", output_dir="--out",
                                      learning_rate="--lr", epochs="--epochs")
        g["output"].update(min_free_gb=0)
        p = tmp_path / f"g-{pre}.yaml"; p.write_text(yaml.safe_dump(g))
        c["roles"][role]["training"] = {"server_train_config": str(p)}
    r = Run(c, clock=FIXED, log=lambda s: None)
    r.run(stop_after="train")
    assert not calls()                                                                              # 서버 접속 없음
    for role, pre in (("LORA_A", "lora_a"), ("LORA_B", "lora_b")):
        assert (r.dir / "data" / role / "server" / f"{pre}_train.jsonl").is_file()
        assert (r.dir / "adapters" / role / "adapter_model.safetensors").is_file()
        rec = json.loads((r.dir / "adapters" / f"{role}.train-wrap.json").read_text())
        assert rec["server_script_sha256"] == sha and rec["command"][1].endswith("fake_server_train.py")
        assert r.rec["stages"]["train"][role]["trainer"] == "server"


def test_empty_upload_root_refusal_says_what_to_decide(srv, cfg, tmp_path):
    raw, write, calls, home, sha = srv
    r = copy.deepcopy(raw); r["role"] = "LORA_A"; r["dataset"].pop("upload_root", None)
    p = tmp_path / "t.yaml"; p.write_text(yaml.safe_dump(r))
    c = copy.deepcopy(cfg); c["training"] = {"trainer": "server"}
    for role in c["roles"]:
        c["roles"][role]["training"] = {"server_train_config": str(p)}
    with pytest.raises(PipelineError, match="사용자가 정해야 할 것.*써도 되는 폴더.*dataset.upload_root"):
        Run(c, clock=FIXED, log=lambda s: None).run(stop_after="train")
