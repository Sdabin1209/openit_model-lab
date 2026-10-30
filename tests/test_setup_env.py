"""U43: scripts/setup-env.sh의 마지막 줄은 계약 SetupEnvReport다(ADR 0025). 진행 기록은 표준 오류."""
import json
import subprocess

from boundary_contracts import validator

from tests.conftest import EXTERNAL_TOOL_GUARD_SECONDS, ROOT


def run_setup():
    return subprocess.run(["bash", "scripts/setup-env.sh"], cwd=ROOT, capture_output=True, text=True, timeout=EXTERNAL_TOOL_GUARD_SECONDS)


def test_last_stdout_line_is_a_valid_setup_env_report():
    r = run_setup()
    assert r.returncode == 0, r.stderr[-1500:]
    rep = json.loads(r.stdout.strip().splitlines()[-1])
    validator("SetupEnvReport").validate(rep)
    assert rep["area"] == "model-lab" and rep["status"] == "ok" and rep["exit_code"] == 0
    assert rep["environment"]["kind"] == "python-venv" and rep["environment"]["path"] == ".venv"
    assert len(r.stdout.strip().splitlines()) == 1          # 진행 기록은 표준 오류로 옮겼다
    assert "[setup-env]" in r.stderr


def test_declared_test_command_runs_the_area_tests_from_the_repo_root():
    """선언한 argv를 리포 루트에서 셸 없이 실행한다. 시험이 자기 자신을 다시 돌리지 않도록 수집만 한다(--collect-only). 전체 실행은 보고에 따로 적는다."""
    rep = json.loads(run_setup().stdout.strip().splitlines()[-1])
    r = subprocess.run([*rep["environment"]["test_command"], "--collect-only", "-q", "--deselect", "tests/test_setup_env.py"], cwd=ROOT, capture_output=True, text=True, timeout=EXTERNAL_TOOL_GUARD_SECONDS)
    assert r.returncode == 0 and "tests/test_registry.py:" in r.stdout, r.stdout[-500:]      # 파일별 수집 개수가 나온다
