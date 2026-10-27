"""학습 서버 정본 감싸기(model-lab train / train-doctor). 서버 스크립트는 대역(fake_server_train.py)으로 대신한다.
진짜 학습·GPU는 시험하지 않는다 [구멍: GPU]."""
import json

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
