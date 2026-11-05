#!/usr/bin/env python3
"""docker를 부르는 모든 스크립트·시험이 쓰는 환경 한 곳. 사용자 전역 ~/.docker 설정(자격 도우미 credsStore)에 기대지 않게 한다.
왜: 이 영역이 받는 기반 이미지는 공개 이미지를 digest로 고정한 것이라 레지스트리 자격이 필요 없다. 전역 설정의 자격 도우미가 응답하지 않으면 `docker build`가 실패한다.
표준 환경 변수 DOCKER_CONFIG로 리포 안 빈 설정({})을 주면 도우미를 부르지 않는다(Docker 공식). 호출자(deploy)가 DOCKER_CONFIG를 이미 주었으면 그대로 쓴다.
DOCKER_CONFIG를 바꾸면 사용자의 docker 컨텍스트(다른 소켓·원격 엔진)도 안 보이므로, 컨텍스트 엔드포인트가 기본 소켓이 아닐 때만 DOCKER_HOST로 옮긴다(kiosk-frontend와 같은 규칙).
사용자 ~/.docker는 읽지도 고치지도 않는다(컨텍스트는 docker CLI에게 묻는다). 설정 폴더는 git 제외 폴더 build/docker-config다.
사용: 파이썬은 `docker_env()`를 subprocess의 env로, 셸은 `eval "$(python3 scripts/docker_env.py --sh)"`."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

DEFAULT_HOST = "unix:///var/run/docker.sock"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "build" / "docker-config"


def docker_env(base=None) -> dict:
    env = dict(os.environ if base is None else base)
    if not env.get("DOCKER_HOST"):
        host = ""
        try:      # 컨텍스트는 바꾸기 전 설정(호출자의 DOCKER_CONFIG 또는 사용자 기본)으로 묻는다. 묻지 못하면(엔진 없음) 기본 소켓으로 둔다
            host = subprocess.run([env.get("DOCKER") or "docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                                  env=env, capture_output=True, text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        if host and host != DEFAULT_HOST:
            env["DOCKER_HOST"] = host
    if not env.get("DOCKER_CONFIG"):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        (CONFIG_DIR / "config.json").write_text("{}\n", encoding="utf-8")
        env["DOCKER_CONFIG"] = str(CONFIG_DIR)
    return env


if __name__ == "__main__" and "--sh" in sys.argv:
    e = docker_env()
    out = [f"export DOCKER_CONFIG={shlex.quote(e['DOCKER_CONFIG'])}"]
    if e.get("DOCKER_HOST"):
        out.append(f"export DOCKER_HOST={shlex.quote(e['DOCKER_HOST'])}")
    print("\n".join(out))
