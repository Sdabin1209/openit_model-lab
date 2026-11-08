"""scripts/docker_env.py: docker 환경 한 곳. 주어진 DOCKER_CONFIG는 그대로, 없으면 리포 안 빈 설정, 사용자 ~/.docker는 쓰지 않는다. 컨텍스트가 기본 소켓이 아니면 DOCKER_HOST로."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import ROOT
from tests.envutil import DOCKER_NAMES, min_env

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


# ---- 실제 docker: 망가진 사용자 전역 설정(가짜 HOME의 credsStore)을 거치지 않는다 ----
def _build(tmp_path, env):
    ctx = tmp_path / "ctx"; ctx.mkdir(exist_ok=True)
    (ctx / "Dockerfile").write_text("# syntax=docker/dockerfile:1\nFROM scratch\nLABEL io.openit.test=docker-env\n")      # syntax 줄: 프런트엔드 이미지를 레지스트리에서 풀어 자격 도우미를 부르게 한다
    tag = f"model-lab-docker-env-test-{os.getpid()}"
    try:
        return subprocess.run(["docker", "build", "-q", "--no-cache", "-t", tag, str(ctx)], capture_output=True, text=True, env=env, timeout=300)
    finally:
        subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, env=de.docker_env())


def _fake_home(tmp_path):
    home = tmp_path / "home"; (home / ".docker").mkdir(parents=True)
    (home / ".docker" / "config.json").write_text(json.dumps({"credsStore": "nonexistent-helper-xyz"}))      # 없는 자격 도우미: 부르는 순간 오류
    return home


import shutil as _sh
import pytest as _pt


@_pt.mark.skipif(_sh.which("docker") is None or subprocess.run(["docker", "info"], capture_output=True, env=__import__("tests.envutil", fromlist=["min_env"]).min_env()).returncode != 0, reason="docker 없음")
def test_docker_build_ignores_a_broken_user_wide_config(tmp_path):
    home = _fake_home(tmp_path)
    # 허용 목록: DOCKER_CONFIG 를 뺀 연결 이름만 받는다(C45, 제외 목록 아님)
    base = {k: os.environ[k] for k in ("PATH", "LANG", "TMPDIR", *(n for n in DOCKER_NAMES if n != "DOCKER_CONFIG")) if k in os.environ}
    ok = _build(tmp_path, de.docker_env({**base, "HOME": str(home)}))
    assert ok.returncode == 0, ok.stderr
    bad = _build(tmp_path, {**base, "HOME": str(home)})      # 변이: 이 환경을 빼면 같은 망가진 설정으로 자격 오류가 난다(시험이 실제로 막는다는 증거)
    assert bad.returncode != 0 and "credentials" in bad.stderr


def test_docker_connection_names_reach_the_child_environment(monkeypatch, tmp_path):
    fake = {n: f"fake-{n.lower()}" for n in DOCKER_NAMES}      # 가짜 값. 출력하지 않는다
    for n, v in fake.items():
        monkeypatch.setenv(n, v)
    env = de.docker_env(min_env(*DOCKER_NAMES))
    assert all(env[n] == v for n, v in fake.items())
    out = subprocess.run([sys.executable, "-c", "import os,sys;sys.exit(0 if all(n in os.environ for n in %r) else 1)" % (DOCKER_NAMES,)], env=env)
    assert out.returncode == 0
