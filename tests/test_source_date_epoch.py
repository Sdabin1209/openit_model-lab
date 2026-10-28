"""T22 후속: created_at의 출처는 --created-at 인자 > SOURCE_DATE_EPOCH > (없으면 거부)다.
기대값은 reproducible-builds.org의 SOURCE_DATE_EPOCH 규격에서 왔다: 값은 1970-01-01T00:00:00Z 이후의 초를 10진 정수로 쓴 UTC 에포크이고,
정수가 아니면 빌드를 실패시켜야 한다(조용히 무시하지 않는다). 1700000000 = 2023-11-14T22:13:20Z, 1000000000 = 2001-09-09T01:46:40Z."""
import json

import pytest

from model_lab.attach import package_adapters
from model_lab.cli import main as cli_main
from model_lab.errors import ConfigError
from tests.test_attach import setup  # noqa: F401  (fixture)


def created(kw, role="LORA_A"):
    return json.loads((kw["out"] / "package" / f"{role}.manifest.json").read_text())["created_at"]


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)


def test_neither_argument_nor_env_is_rejected_and_message_names_both(setup, clean_env):
    kw, _ = setup
    with pytest.raises(ConfigError) as e:
        package_adapters(**kw, publish=False)
    assert "--created-at" in str(e.value) and "SOURCE_DATE_EPOCH" in str(e.value)
    assert not (kw["out"] / "package").exists()               # 아무것도 만들지 않는다


def test_env_only_gives_that_time(setup, monkeypatch):
    kw, _ = setup
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    package_adapters(**kw, publish=False)
    assert created(kw) == "2023-11-14T22:13:20Z"


def test_argument_wins_over_env(setup, monkeypatch):
    kw, _ = setup
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    package_adapters(**kw, publish=False, created_at="2001-09-09T01:46:40Z")
    assert created(kw) == "2001-09-09T01:46:40Z"


@pytest.mark.parametrize("bad", ["", "abc", "17e8", "-5", "1.5", "2023-11-14"])
def test_invalid_env_is_rejected_not_ignored(setup, monkeypatch, bad):
    kw, _ = setup
    monkeypatch.setenv("SOURCE_DATE_EPOCH", bad)
    with pytest.raises(ConfigError):
        package_adapters(**kw, publish=False)


def test_cli_follows_the_same_priority(setup, monkeypatch, capsys):
    kw, _ = setup
    argv = ["package-adapters", "--config", str(kw["config"]), "--profile", "gpu-dev-12g",
            "--adapter-a", str(kw["adapters"]["LORA_A"]), "--adapter-b", str(kw["adapters"]["LORA_B"]),
            "--prompt-a", str(kw["prompts"]["LORA_A"]), "--prompt-b", str(kw["prompts"]["LORA_B"]),
            "--dataset-id", "openit-dataset", "--dataset-version", "v1", "--dataset-hash", "sha256:" + "b" * 64,
            "--out", str(kw["out"]), "--no-publish"]
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    assert cli_main(argv) == 2
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    assert cli_main(argv) == 0 and created(kw) == "2023-11-14T22:13:20Z"
    assert cli_main(argv + ["--created-at", "2001-09-09T01:46:40Z"]) == 0 and created(kw) == "2001-09-09T01:46:40Z"
