import shutil
from pathlib import Path

import pytest
import yaml

from tests.doubles.dataset_store import DirDatasetStore, MemoryDatasetStore
from tests.doubles.fixtures import build_dataset

ROOT = Path(__file__).resolve().parents[1]
# vendor/survey-schema 사본이 있으면 먼저 쓰고(GPU 호스트), 없으면 태그로 설치된 survey_schema 패키지의 스키마 폴더를 쓴다.
_vendor = ROOT / "vendor" / "survey-schema" / "schemas"


def _installed_schema_dir() -> Path:
    import survey_schema
    return Path(survey_schema.contract_version_dir(1)) / "schemas"


SCHEMA_DIR = _vendor if _vendor.is_dir() else _installed_schema_dir()


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
    for r in raw["roles"].values():      # 설정 파일을 tmp로 옮기므로 상대 경로를 절대 경로로
        r["system_prompt_file"] = str((ROOT / "configs" / r["system_prompt_file"]).resolve())
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(raw))
    return load_config(p)


@pytest.fixture
def store(fixture_store):
    return DirDatasetStore(str(fixture_store))


FIXED = lambda: "2026-10-15T00:00:00Z"  # noqa: E731


@pytest.fixture(autouse=True)
def _source_date_epoch(monkeypatch):
    """package-adapters는 created_at의 출처가 없으면 거부한다(ADR 0020). 다른 시험은 고정 시각을 SOURCE_DATE_EPOCH로 준다.
    출처가 없을 때를 시험하는 tests/test_source_date_epoch.py는 이 값을 지운다."""
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
