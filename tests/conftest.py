import shutil
from pathlib import Path

import pytest
import yaml

from tests.doubles.dataset_store import DirDatasetStore, MemoryDatasetStore
from tests.doubles.fixtures import build_dataset

ROOT = Path(__file__).resolve().parents[1]
_sib = (ROOT / ".." / "survey-service" / "schema" / "schemas").resolve()
# GPU 호스트에는 이웃 폴더가 없어 vendor/에 복사해 둔 문진 스키마(읽기 전용 사본)를 쓴다.
SCHEMA_DIR = _sib if _sib.is_dir() else (ROOT / "vendor" / "survey-schema" / "schemas")


@pytest.fixture(scope="session")
def fixture_store(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("store")
    build_dataset(d, "fx-golden", "v1")
    return d


@pytest.fixture
def cfg(tmp_path, fixture_store):
    """dry-run 설정(configs/dryrun.yaml)을 tmp 경로로 옮긴 최종 설정."""
    from model_lab.config import load_config
    raw = yaml.safe_load((ROOT / "configs" / "dryrun.yaml").read_text())
    raw["run"]["runs_dir"] = str(tmp_path / "runs")
    raw["registry"]["path"] = str(tmp_path / "registry")
    raw["inputs"]["schema_dir"] = str(SCHEMA_DIR)
    raw["inputs"]["dataset_store"]["args"]["root"] = str(fixture_store)
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(raw))
    return load_config(p)


@pytest.fixture
def store(fixture_store):
    return DirDatasetStore(str(fixture_store))


FIXED = lambda: "2026-10-15T00:00:00Z"  # noqa: E731
