"""C2: 설정은 YAML 1.2 코어 스키마로 읽고 중복 키는 거부한다.
기대값은 YAML 1.2.2 사양(10.3.2 코어 스키마 태그 해석, 3.2.1.1 매핑 키 유일성)에서 가져왔다:
- 불리언은 true/True/TRUE/false/False/FALSE뿐이다. off, no, yes, on, y, n은 문자열이다.
- 정수는 [-+]?[0-9]+ (10진, 앞의 0을 8진으로 보지 않음), 0o[0-7]+ (8진), 0x[0-9a-fA-F]+ 이다.
- 매핑의 키는 유일해야 한다."""
import pytest

from model_lab.config import _read
from model_lab.wiring.errors import ConfigError


def load(tmp_path, text):
    p = tmp_path / "c.yaml"
    p.write_text(text)
    return _read(p)


@pytest.mark.parametrize("word", ["off", "no", "yes", "on", "Off", "NO", "y", "n"])
def test_yaml11_booleans_stay_strings(tmp_path, word):
    assert load(tmp_path, f"a: {word}\n")["a"] == word


@pytest.mark.parametrize("word,val", [("true", True), ("True", True), ("TRUE", True), ("false", False), ("False", False), ("FALSE", False)])
def test_yaml12_booleans(tmp_path, word, val):
    assert load(tmp_path, f"a: {word}\n")["a"] is val


def test_leading_zero_is_decimal_not_octal(tmp_path):
    d = load(tmp_path, "a: 0123\nb: 0o17\nc: 0x1F\nd: -5\ne: 1e3\n")
    assert d["a"] == 123 and d["b"] == 15 and d["c"] == 31 and d["d"] == -5 and d["e"] == 1000.0


def test_null_forms(tmp_path):
    d = load(tmp_path, "a: null\nb: ~\nc:\n")
    assert d == {"a": None, "b": None, "c": None}


def test_duplicate_key_is_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load(tmp_path, "a: 1\na: 2\n")


def test_nested_duplicate_key_is_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load(tmp_path, "x:\n  a: 1\n  b: 2\n  a: 3\n")


def test_disk_policy_off_is_read_as_the_documented_string(tmp_path):
    """runbook이 권하는 output.disk_policy: off 가 따옴표 없이도 문자열 'off'로 읽힌다."""
    from model_lab.wiring.train_flow import load_train_config
    import yaml, pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    raw = (root / "configs" / "train.yaml").read_text()
    raw = raw.replace("  min_free_gb: 30\n", "  min_free_gb: 30\n  disk_policy: off\n")
    p = tmp_path / "t.yaml"
    p.write_text(raw)
    assert load_train_config(p)["output"]["disk_policy"] == "off"
