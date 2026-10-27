"""학습 서버 정본 감싸기(model-lab train / train-doctor). 서버 스크립트는 대역(fake_server_train.py)으로 대신한다.
진짜 학습·GPU는 시험하지 않는다 [구멍: GPU]."""
import json
import shlex
from pathlib import Path

import pytest
import yaml

from model_lab import train_wrap as tw
from model_lab.cli import main as cli_main
from model_lab.errors import ConfigError, PipelineError
from tests.conftest import ROOT

STUB = ROOT / "tests" / "doubles"


@pytest.fixture
def env(tmp_path):
    ds = tmp_path / "ds"; ds.mkdir()
    for s in tw.SPLITS:
        (ds / f"lora_a_{s}.jsonl").write_text('{"messages": []}\n')
    raw = yaml.safe_load((ROOT / "configs" / "train.yaml").read_text())
    raw["server"].update(code_dir=str(STUB), scripts={"LORA_A": "fake_server_train.py", "LORA_B": "fake_server_train.py"},
                         python="python3", require_gpu=False, args_verified=True, python_modules=["json"])
    raw["server"]["arg_map"] = {k: None for k in raw["server"]["arg_map"]}   # 대역은 자기 인자 이름을 쓴다
    raw["base_model"].update(id="Qwen/Qwen3-4B", profile="qwen3-4b")
    raw["server"]["arg_map"].update(base_model="--model", dataset_dir="--data_dir", dataset_prefix="--prefix", output_dir="--out",
                                    learning_rate="--lr", epochs="--epochs")
    raw["dataset"].update(dir=str(ds), prefix="lora_a")
    raw["output"].update(adapter_dir=str(tmp_path / "run" / "adapter"), min_free_gb=0)
    path = tmp_path / "train.yaml"

    def write(r=raw):
        path.write_text(yaml.safe_dump(r)); return path
    write()
    return raw, write, tmp_path


def test_shipped_config_loads_and_dry_run_prints_plan(capsys):
    assert cli_main(["train", "--config", str(ROOT / "configs" / "train.yaml"), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "train_lora_a.py" in out and "expected_adapter_dir" in out and "실행하지 않았다" in out
    assert cli_main(["train", "--config", str(ROOT / "configs" / "train-b.yaml"), "--dry-run"]) == 0
    assert "train_lora_b.py" in capsys.readouterr().out


def test_command_uses_arg_map_and_never_runs_on_dry_run(env):
    raw, write, tmp = env
    cfg = tw.load_train_config(write())
    pl = tw.plan(cfg)
    c = pl["command"]
    assert c[1].endswith("fake_server_train.py") and c[c.index("--lr") + 1] == "0.0002" and c[c.index("--epochs") + 1] == "3"
    assert c[c.index("--model") + 1] == "Qwen/Qwen3-4B"
    assert any("lora_r=16" in n for n in pl["notes"])          # 인자 이름이 없는 값은 기록만 한다
    assert not (tmp / "run").exists()
    assert cli_main(["train", "--config", str(write()), "--dry-run"]) == 0 and not (tmp / "run").exists()


def test_run_with_fake_trainer_writes_adapter_and_record(env, capsys):
    raw, write, tmp = env
    cfg = tw.load_train_config(write())
    rec = tw.run_train(cfg, log=lambda m: None)
    a = tmp / "run" / "adapter"
    assert (a / "adapter_model.safetensors").is_file() and rec["adapter"]["r"] == 16
    saved = json.loads(tw.record_path(cfg).read_text())
    assert saved["role"] == "LORA_A" and saved["exit_code"] == 0 and saved["dataset"]["id"] == "dataset_10_14"
    assert set(saved["dataset"]["file_sha256"]) == {f"lora_a_{s}.jsonl" for s in tw.SPLITS}
    assert json.loads((tmp / "run" / "args.json").read_text()) == {"lr": 0.0002, "epochs": 3}
    assert "package-adapters" in tw.next_step(cfg)


def test_output_feeds_package_adapters_check(env):
    raw, write, tmp = env
    cfg = tw.load_train_config(write())
    tw.run_train(cfg, log=lambda m: None)
    from model_lab.attach import check_adapter, load_profile
    prof = load_profile(cfg["base_model"]["profile"])
    check_adapter("LORA_A", tmp / "run" / "adapter", prof["base_model"], prof["checks"]["max_lora_rank"])


def test_failures(env):
    raw, write, tmp = env
    raw["server"]["extra_args"] = ["--fail"]
    with pytest.raises(PipelineError, match="종료 코드 3"):
        tw.run_train(tw.load_train_config(write()), log=lambda m: None)
    raw["server"]["extra_args"] = ["--wrong_base"]
    with pytest.raises(PipelineError, match="기반 모델"):
        tw.run_train(tw.load_train_config(write()), log=lambda m: None)
    raw["server"]["extra_args"] = []
    raw["server"]["code_dir"] = str(tmp / "nope")
    with pytest.raises(PipelineError, match="서버 학습 스크립트가 없다"):
        tw.run_train(tw.load_train_config(write()), log=lambda m: None)


def test_gpu_hole_blocks_real_run_but_not_dry_run(env, monkeypatch):
    raw, write, tmp = env
    raw["server"]["require_gpu"] = True
    monkeypatch.setattr(tw, "gpu_state", lambda: (False, "없음"))
    cfg = tw.load_train_config(write())
    with pytest.raises(PipelineError, match="구멍: GPU"):
        tw.run_train(cfg, log=lambda m: None)
    assert tw.plan(cfg)["command"]


@pytest.mark.parametrize("mut,msg", [
    (lambda r: r["server"].update(api_token="x"), "비밀|형식 위반"),
    (lambda r: r["server"].update(arg_map={"bogus": "--x"}), "쓰이지 않는 이름"),
    (lambda r: r["server"]["arg_map"].update(seed="--model"), "두 번"),
    (lambda r: r["base_model"].update(id="Qwen/Qwen3-8B"), "프로파일"),
    (lambda r: r.update(role="LORA_C"), "형식 위반"),
    (lambda r: r["dataset"].pop("version"), "형식 위반"),
])
def test_config_rejections(env, mut, msg):
    raw, write, tmp = env
    mut(raw)
    with pytest.raises(ConfigError, match=msg):
        tw.load_train_config(write())


def test_inline_secret_rejected_in_hyperparams(env):
    raw, write, tmp = env
    raw["hyperparams"]["hf_token"] = "abc"
    with pytest.raises(ConfigError, match="비밀"):
        tw.load_train_config(write())


def test_doctor_reports_hole_not_fail_for_gpu(env, monkeypatch):
    raw, write, tmp = env
    monkeypatch.setattr(tw, "gpu_state", lambda: (False, "없음"))
    rows = {n: st for st, n, _ in tw.doctor(tw.load_train_config(write()))}
    assert rows["GPU"] == "HOLE" and rows["서버 스크립트"] == "OK" and rows["데이터셋"] == "OK" and rows["파이썬 의존"] == "OK"
    assert rows["서버 인자 이름"] == "OK" and rows["디스크"] == "OK"
    assert "근거" in {n: m for _, n, m in tw.doctor(tw.load_train_config(write()))}["서버 인자 이름"]
    raw["server"]["args_verified"] = False
    assert {n: st for st, n, _ in tw.doctor(tw.load_train_config(write()))}["서버 인자 이름"] == "HOLE"


def test_doctor_fail_exit_code_and_guidance(env, capsys):
    raw, write, tmp = env
    raw["server"]["code_dir"] = str(tmp / "nope"); raw["server"]["python_modules"] = ["no_such_module_xyz"]
    assert cli_main(["train-doctor", "--config", str(write())]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] 서버 스크립트" in out and "no_such_module_xyz" in out


def test_hf_copy_warns_not_canonical():
    from model_lab.trainers import make_trainer
    with pytest.warns(UserWarning, match="정본이 아니다"):
        make_trainer("hf")


# ---- 실제 서버 스크립트 인자(헤드가 add_argument를 읽어 확인, 2026-10-27)와 v2 학습 run_config.json 대조 ----
# 아래 값은 서버 run_config.json의 값이다. out_dir은 서버가 자기 작업 폴더 기준 상대 경로로 기록한 값이다.
RUN_CONFIG_A = {"data_dir": "/home/openit/taehyeon/data/dataset_10_14", "prefix": "lora_a", "model": "Qwen/Qwen3-8B", "out_dir": "runs/a_8b_v2",
                "max_len": 4096, "epochs": 3, "lr": 0.0002, "batch": 2, "accum": 4, "rank": 16, "alpha": 16, "dropout": 0.0,
                "eval_steps": 50, "seed": 42, "dry_run": False}
RUN_CONFIG_B = {**RUN_CONFIG_A, "data_dir": "/home/openit/toy dataset/dataset_10_14", "prefix": "lora_b", "out_dir": "runs/b_8b_1014"}
SERVER_TYPES = {"data_dir": str, "prefix": str, "model": str, "out_dir": str, "max_len": int, "epochs": int, "lr": float, "batch": int,
                "accum": int, "rank": int, "alpha": int, "dropout": float, "eval_steps": int, "seed": int}   # dry_run은 값 없는 플래그


def server_parse(cmd):
    """서버 스크립트의 argparse를 흉내 낸다(이름·타입은 확인한 add_argument 그대로). 모르는 인자·값 누락은 실패."""
    got, i = {"dry_run": False}, 2
    while i < len(cmd):
        name = cmd[i].removeprefix("--")
        if name == "dry_run":
            got[name] = True; i += 1; continue
        assert cmd[i].startswith("--") and name in SERVER_TYPES and name not in got and i + 1 < len(cmd), cmd[i:]
        got[name] = SERVER_TYPES[name](cmd[i + 1]); i += 2
    return got


@pytest.mark.parametrize("fname,run,code_dir", [("train.yaml", RUN_CONFIG_A, "/home/openit/taehyeon"),
                                                ("train-b.yaml", RUN_CONFIG_B, "/home/openit/myungchan")])
def test_shipped_configs_match_server_run_config(fname, run, code_dir, tmp_path):
    raw = yaml.safe_load((ROOT / "configs" / fname).read_text())
    assert raw["server"]["args_verified"] is True and raw["server"]["args_evidence"]
    raw["server"]["code_dir"] = code_dir                                   # ~는 이 PC의 홈으로 풀리므로 서버 절대 경로로 고정
    raw["output"]["adapter_dir"] = str(Path(code_dir) / run["out_dir"])    # 서버 상대 out_dir을 절대 경로로 쓴 것
    path = tmp_path / "t.yaml"; path.write_text(yaml.safe_dump(raw, sort_keys=False))
    cfg = tw.load_train_config(path)
    cmd, notes = tw.build_command(cfg)
    assert notes == []                                                     # 넘기지 못한 값도, 미확인 주의도 없다
    got = server_parse(cmd)
    out = got.pop("out_dir"); want = dict(run); want_out = want.pop("out_dir")
    assert got == want and out == str(Path(code_dir) / want_out)
    assert cmd[0] == "python3" and cmd[1] == f"{code_dir}/train_lora_{raw['role'][-1].lower()}.py"
    assert tw.plan(cfg)["cwd"] == code_dir


def test_command_order_is_stable_and_spaced_path_is_one_argument(tmp_path):
    raw = yaml.safe_load((ROOT / "configs" / "train-b.yaml").read_text())
    raw["server"]["code_dir"] = "/home/openit/myungchan"
    path = tmp_path / "t.yaml"; path.write_text(yaml.safe_dump(raw, sort_keys=False))
    cfg = tw.load_train_config(path)
    c1, c2 = tw.build_command(cfg)[0], tw.build_command(tw.load_train_config(path))[0]
    assert c1 == c2
    flags = [x for x in c1[2:] if x.startswith("--")]
    assert flags == ["--model", "--data_dir", "--prefix", "--out_dir", "--lr", "--epochs", "--batch", "--accum", "--max_len", "--seed",
                     "--rank", "--alpha", "--dropout", "--eval_steps"]
    assert c1[c1.index("--data_dir") + 1] == "/home/openit/toy dataset/dataset_10_14"      # 공백이 든 경로는 인자 하나
    assert shlex.split(tw.plan(cfg)["command_line"]) == c1                                # 보여 주는 줄을 셸에 붙여 넣어도 같다
    assert "'/home/openit/toy dataset/dataset_10_14'" in tw.plan(cfg)["command_line"]


def test_boolean_flag_has_no_value(tmp_path):
    raw = yaml.safe_load((ROOT / "configs" / "train.yaml").read_text())
    path = tmp_path / "t.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    assert "--dry_run" not in tw.build_command(tw.load_train_config(path))[0]          # false: 넘기지 않는다
    raw["hyperparams"]["dry_run"] = True
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    c = tw.build_command(tw.load_train_config(path))[0]
    assert c.count("--dry_run") == 1 and c[c.index("--dry_run") + 1:] == []            # true: 값 없이 마지막 위치에 플래그만
    assert server_parse(c)["dry_run"] is True
    assert "true" not in c and "True" not in c
