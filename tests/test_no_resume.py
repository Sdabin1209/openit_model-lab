"""서버 정본 학습 스크립트는 재개를 지원하지 않는다(ADR 0030): resume 항목이 있는 설정은 거부한다. 재개를 지원하는 척하는 설정 파일도 없어야 한다."""
import copy
import pytest
import yaml

from model_lab.cli import main as cli_main
from model_lab.config import ConfigError, load_train_config
from tests.conftest import ROOT

MSG = "정본 학습 스크립트는 재개를 지원하지 않는다"


def _write(tmp_path, mutate):
    raw = yaml.safe_load((ROOT / "configs" / "train.yaml").read_text())
    mutate(raw)
    p = tmp_path / "t.yaml"; p.write_text(yaml.safe_dump(raw)); return p


@pytest.mark.parametrize("mutate", [
    lambda r: r["hyperparams"].update(resume_from_checkpoint="x"),
    lambda r: r.update(resume=True),
    lambda r: r["server"]["arg_map"].update(epochs="--resume"),
    lambda r: r["server"].update(extra_args=["--resume_from", "runs/x"]),
])
def test_train_config_with_resume_is_refused_with_how_to_fix(tmp_path, mutate, capsys):
    p = _write(tmp_path, mutate)
    with pytest.raises(ConfigError, match=MSG):
        load_train_config(p)
    assert cli_main(["train", "--config", str(p), "--dry-run"]) == 2           # 실행 경로(dry-run 포함)도 같은 거부
    assert "처음부터 학습하려면 resume 항목을 지운다" in capsys.readouterr().err


def test_no_resume_config_files_or_resume_keys_ship():
    assert not list((ROOT / "configs").glob("*resume*"))
    for f in (ROOT / "configs").glob("*.yaml"):
        assert "resume" not in f.read_text().lower(), f
