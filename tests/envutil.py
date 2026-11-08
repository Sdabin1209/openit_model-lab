"""시험이 하위 프로세스에 넘기는 환경(RUNTIME R9.1: 상속받은 환경 변수를 통째로 쓰거나 넘기지 않는다).
허용 목록은 head check-container-inputs 의 최소 집합과 같다. 그 밖에 호출이 실제로 필요한 이름은 호출부가 이름으로 더한다(extra_names)."""
import os

ALLOWED = ("PATH", "HOME", "LANG", "TMPDIR", "DOCKER_CONFIG", "DOCKER_HOST")
# 왜: 태그 설치·얕은 clone 은 GitHub ssh 접근이 필요하다(CLAUDE.md 컨테이너와 개발: 태그 설치). 인증 수단을 가리키는 이름만 더한다(값은 출력하지 않는다)
GIT_NAMES = ("SSH_AUTH_SOCK", "GIT_CONFIG_GLOBAL", "GIT_SSH_COMMAND")


def min_env(*extra_names: str, **extra) -> dict:
    env = {k: os.environ[k] for k in (*ALLOWED, *extra_names) if k in os.environ}
    env.update(extra)
    return env

# 왜: docker 호출은 엔진 연결에 필요한 이름이 있어야 한다(원격 엔진·TLS·ssh 컨텍스트). 이름만 허용하고 값은 출력하지 않는다(codex C2 검토, 2026-11-09)
DOCKER_NAMES = ("DOCKER_CONFIG", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "SSH_AUTH_SOCK")
