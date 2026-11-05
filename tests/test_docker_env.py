"""scripts/docker_env.py: docker 환경 한 곳. 주어진 DOCKER_CONFIG는 그대로, 없으면 리포 안 빈 설정, 사용자 ~/.docker는 쓰지 않는다. 컨텍스트가 기본 소켓이 아니면 DOCKER_HOST로."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import ROOT

_spec = importlib.util.spec_from_file_location("docker_env_mod", ROOT / "scripts" / "docker_env.py")
de = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(de)


def test_given_docker_config_is_kept_as_is(tmp_path):
    e = de.docker_env({"PATH": os.environ["PATH"], "DOCKER_CONFIG": str(tmp_path), "DOCKER_HOST": "unix:///x.sock"})
    assert e["DOCKER_CONFIG"] == str(tmp_path) and e["DOCKER_HOST"] == "unix:///x.sock"


def test_without_it_the_repo_empty_config_is_used_and_user_home_docker_is_not(tmp_path):
    e = de.docker_env({"PATH": os.environ["PATH"], "HOME": str(tmp_path), "DOCKER_HOST": "unix:///var/run/docker.sock"})
    cfg = Path(e["DOCKER_CONFIG"])
    assert cfg == ROOT / "build" / "docker-config" and json.loads((cfg / "config.json").read_text()) == {}
    assert not str(cfg).startswith(str(Path.home() / ".docker"))
    assert not (tmp_path / ".docker").exists()      # 사용자 홈에 아무것도 만들지 않는다


def test_non_default_context_endpoint_moves_to_docker_host(tmp_path):
    fake = tmp_path / "docker"
    fake.write_text("#!/bin/sh\necho unix:///run/user/1000/docker.sock\n"); fake.chmod(0o755)
    e = de.docker_env({"PATH": os.environ["PATH"], "DOCKER": str(fake)})
    assert e["DOCKER_HOST"] == "unix:///run/user/1000/docker.sock"
    fake.write_text("#!/bin/sh\necho unix:///var/run/docker.sock\n")
    assert "DOCKER_HOST" not in de.docker_env({"PATH": os.environ["PATH"], "DOCKER": str(fake)})


def test_shell_form_exports_what_the_scripts_eval(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "docker_env.py"), "--sh"], capture_output=True, text=True,
                       env={"PATH": os.environ["PATH"], "DOCKER_CONFIG": str(tmp_path)}, timeout=60)
    assert r.returncode == 0 and f"export DOCKER_CONFIG={tmp_path}" in r.stdout
