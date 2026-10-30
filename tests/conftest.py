import shutil
from pathlib import Path

import pytest
import yaml

from tests.doubles.dataset_store import DirDatasetStore, MemoryDatasetStore
from tests.doubles.fixtures import build_dataset

ROOT = Path(__file__).resolve().parents[1]
# 매달림 감시(판정이 아니다): 가짜에게서 신호를 기다리거나 외부 프로세스를 돌릴 때, 코드가 진짜로 멈췄을 때만 걸리는 바깥 마감. 시험에서 숫자 timeout은 이 상수뿐이다.
# 300초 = 정상 경로(가짜와의 신호는 즉시, 소켓 왕복은 수 ms)의 수만 배. 시험이 시간을 재서 판정하는 곳은 없다(tests/TIMING.md, ADR 0029).
HANG_GUARD_SECONDS = 300
# 외부 도구(docker 빌드, pip 설치)는 정상이 수십~백 초라 300초가 정상 경로의 수십 배에 그친다. 같은 상수의 배수로 두어 숫자를 새로 만들지 않는다.
EXTERNAL_TOOL_GUARD_SECONDS = HANG_GUARD_SECONDS * 12
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
