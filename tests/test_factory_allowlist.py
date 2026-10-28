"""C6: 설정의 `module:callable`은 허용 목록에 있는 이름만 부를 수 있다. 목록 밖 이름(표준 라이브러리의 임의 호출 대상 포함)은 호출 전에 거부한다."""
import pytest

from model_lab import factories
from model_lab.errors import ConfigError


@pytest.mark.parametrize("spec", [{"factory": "os:getcwd"}, {"factory": "subprocess:getoutput", "args": {"cmd": "true"}},
                                  {"factory": "builtins:print"}, {"factory": "tests.doubles.dataset_store:from_dir_x"}])
def test_names_outside_allowlist_are_rejected_before_import(spec, monkeypatch):
    called = []
    monkeypatch.setattr(factories.importlib, "import_module", lambda m: called.append(m))
    with pytest.raises(ConfigError):
        factories.build(spec)
    assert called == []                                 # import조차 하지 않는다(import 부작용도 막는다)


def test_every_factory_used_by_shipped_configs_is_allowed():
    import yaml
    from tests.conftest import ROOT
    names = set()

    def walk(x):
        if isinstance(x, dict):
            if isinstance(x.get("factory"), str):
                names.add(x["factory"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    for p in (ROOT / "configs").rglob("*.yaml"):
        walk(yaml.safe_load(p.read_text()))
    assert names and names <= factories.ALLOWED_FACTORIES
