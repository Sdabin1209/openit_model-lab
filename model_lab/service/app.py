"""model-registry 서비스 진입점: 환경 변수를 읽어 link_registry_serve 컴포넌트를 기동한다(진입점이라 업무 규칙을 두지 않는다).
구 app.py 설명: model-registry 서비스(I3 서버). HTTP.md의 I3 표와 CONVENTIONS 9절을 따른다. WSGI(PEP 3333) 앱을 waitress로 서빙한다(ADR 0023).

환경 변수(진입점 `python -m model_lab.service`가 읽는다). 비밀 두 개는 boundary_contracts.read_secret(D84)으로 읽는다:
  MODEL_REGISTRY_READ_TOKENS_FILE / MODEL_REGISTRY_READ_TOKENS    역할=토큰,역할=토큰  (읽기 경로만)
  MODEL_REGISTRY_WRITE_TOKENS_FILE / MODEL_REGISTRY_WRITE_TOKENS  model-lab=토큰         (쓰기 경로. 역할은 model-lab만 허용)
  (NAME_FILE이 있으면 그 파일 내용, 없으면 NAME 환경 변수. 운영은 파일, 환경 변수는 개발용)
  MODEL_REGISTRY_ROOT          저장 볼륨(기본 /data)
  MODEL_REGISTRY_PORT          기본 8080 (0이면 빈 포트)
  MODEL_REGISTRY_MAX_BODY      게시 본문 최대 바이트(기본 4GiB)
  MODEL_REGISTRY_SOCKET_TIMEOUT  waitress channel_timeout: 요청이 진행 중이지 않은 연결(유휴, 요청을 다 보내지 않은 연결)을 닫는 초(기본 30, ML-1)
  MODEL_REGISTRY_MAX_CONNECTIONS waitress connection_limit: 동시 연결 상한(기본 32; 넘으면 새 연결은 받지 않고 backlog에서 기다린다. 503을 주지 않는다, ADR 0023)
  MODEL_REGISTRY_THREADS         waitress 작업 스레드 수(기본 16, ADR 0023)
  MODEL_REGISTRY_MAX_PUBLISHES   동시 게시 상한(기본 1; 넘으면 503 UNAVAILABLE, 디스크와 .tmp를 지킨다)
  MODEL_REGISTRY_AUTH_FAIL_LIMIT 같은 주소의 인증 실패 상한(기본 10회 / 60초; 넘으면 그 주소는 잠시 503, 상수 시간 비교와 별개의 시도 제한)
  MODEL_REGISTRY_SHUTDOWN_GRACE  SIGTERM 뒤 진행 중 요청을 기다리는 초(기본 8; 유휴 연결은 즉시 닫는다, Docker 기본 정지 유예 10초보다 짧게)
  MODEL_REGISTRY_MIN_FREE_GB     게시 전 요구 여유 공간 GB(기본 1; 본문 크기의 2배 + 이 값이 모자라면 503, ML-2)
로그는 표준 출력으로, 토큰과 본문은 남기지 않는다."""
from __future__ import annotations

import os
import shutil
import signal
import sys
import threading
from pathlib import Path

from boundary_contracts import SecretError, read_secret

from ..components import link_registry_serve as _serve
from ..components.link_registry_serve import drain, make_server  # noqa: F401  (re-export: 시험이 쓴다)
from ..components.model_registry import FileModelRegistry


class Service(_serve.Service):
    """진입점의 연결: link_registry_serve에 파일 레지스트리(model_registry)를 주입한다. 풀어 둔 임시 폴더에서 하드 링크로 올려 디스크를 아낀다."""

    def __init__(self, root, read_tokens, write_tokens, **kw):
        kw.setdefault("registry_factory", lambda r: FileModelRegistry(r, link_on_commit=True))
        super().__init__(root, read_tokens, write_tokens, **kw)

def parse_tokens(spec: str | None) -> dict[str, str]:
    """'역할=토큰,역할=토큰' -> {토큰: 역할}"""
    out: dict[str, str] = {}
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError("토큰 설정은 역할=토큰 형식이어야 한다")
        role, tok = part.split("=", 1)
        if not role or not tok:
            raise ValueError("역할과 토큰은 비어 있을 수 없다")
        out[tok] = role
    return out


# 토큰 최소 길이 32자. 무차별 대입에 대한 실제 방어는 토큰의 엔트로피이고(실패 제한은 아래 Service 주석처럼 로그 스팸·CPU 보호일 뿐이다),
# 32자 이상의 무작위 16진수는 128비트 이상이다. 근거(ADR 0009): 알고 있는 일반 지침 — OWASP 세션 관리 치트시트(예측 불가능한 ID는 CSPRNG로
# 만들고 최소 64비트, 권장은 128비트 안팎)와 NIST SP 800-63B(무작위 인증 비밀의 엔트로피 하한)를 참고했다. 이번에 원문을 다시 확인하지는 않았다.
# 길이는 엔트로피를 보증하지 못하므로 무작위로 만들 책임은 운영자 몫이다(`openssl rand -hex 32`).
MIN_TOKEN_LEN = 32


def check_token_strength(tokens: dict[str, str], *, min_len: int = MIN_TOKEN_LEN) -> None:
    """시작 때 호출한다. 너무 짧거나 거의 한 글자로만 된 토큰이면 ValueError(토큰 값은 메시지에 넣지 않는다)."""
    for tok, role in tokens.items():
        if len(tok) < min_len:
            raise ValueError(f"역할 {role}의 토큰이 {min_len}자 미만이다. `openssl rand -hex 32` 같은 무작위 값을 쓴다")
        if len(set(tok)) < 8:
            raise ValueError(f"역할 {role}의 토큰이 단조롭다(서로 다른 글자 8개 미만). 무작위 값을 쓴다")


# 아래 기본값의 근거(ADR 0007): 모두 측정하지 않은 보수적 시작값이다. 조정은 환경 변수로 한다. 근거 출처 없음 = 측정 안 함.
#  channel_timeout 30초: 요청을 다 보내지 않았거나 유휴인 연결을 닫는 시간. 측정 안 함(기존 소켓 timeout 값을 그대로 옮김, ADR 0007·0023).
#  동시 연결 32: 소비자가 release-gate 등 소수이고 게시는 1건뿐이라는 가정(측정 안 함); waitress connection_limit(기존 값을 그대로 옮김).
#  스레드 16: 느린 다운로드가 스레드를 잡아도 /healthz가 막히지 않을 여유. waitress 기본 4는 너무 작다고 보았다. 측정 안 함(ADR 0023).
#  동시 게시 1: 게시는 어차피 파일 잠금으로 직렬화되고(registry.py) 본문이 최대 4GiB라 대기시키면 디스크·스레드를 오래 잡는다.
#  인증 실패 10회/60초: 사람이 토큰을 잘못 넣는 정도는 허용하고 반복 추측만 막는 값(측정 안 함, 표준 값 아님).
#  여유 공간 = 본문 2배 + 1GB: 본문 tar와 풀어 둔 사본이 동시에 있는 최대 순간(저장은 하드 링크라 추가 사본 없음) + 여유 1GB(근거 기록 없음).
#  종료 유예 8초(MODEL_REGISTRY_SHUTDOWN_GRACE): Docker의 기본 정지 유예 10초(SIGKILL까지)보다 짧게 잡아 SIGKILL 전에 정리를 끝낸다. 8은 측정 안 함.
#  본문 4GiB(MODEL_REGISTRY_MAX_BODY): 어댑터 크기 상한의 여유값. 근거 기록 없음(측정 안 함); 14B bf16이 아닌 LoRA 어댑터는 이보다 훨씬 작다고 가정(추정).
def main(env=os.environ) -> int:
    try:
        read, write = (parse_tokens(read_secret("MODEL_REGISTRY_READ_TOKENS", environ=env)),
                       parse_tokens(read_secret("MODEL_REGISTRY_WRITE_TOKENS", environ=env)))
        if not read and not write:
            raise ValueError("MODEL_REGISTRY_READ_TOKENS 또는 MODEL_REGISTRY_WRITE_TOKENS가 필요하다")
        # 개발·시험에서 짧은 토큰이 꼭 필요하면 MODEL_REGISTRY_MIN_TOKEN_LEN으로 낮출 수 있다(운영에서는 쓰지 않는다)
        check_token_strength({**read, **write}, min_len=int(env.get("MODEL_REGISTRY_MIN_TOKEN_LEN", MIN_TOKEN_LEN)))
        svc = Service(Path(env.get("MODEL_REGISTRY_ROOT", "/data")), read, write,
                      max_body=int(env.get("MODEL_REGISTRY_MAX_BODY", 4 << 30)),
                      socket_timeout=float(env.get("MODEL_REGISTRY_SOCKET_TIMEOUT", 30)),
                      max_connections=int(env.get("MODEL_REGISTRY_MAX_CONNECTIONS", 32)),
                      max_publishes=int(env.get("MODEL_REGISTRY_MAX_PUBLISHES", 1)),
                      auth_fail_limit=int(env.get("MODEL_REGISTRY_AUTH_FAIL_LIMIT", 10)),
                      min_free_gb=float(env.get("MODEL_REGISTRY_MIN_FREE_GB", 1)),
                      shutdown_grace=float(env.get("MODEL_REGISTRY_SHUTDOWN_GRACE", 8)))
    except (ValueError, OSError, SecretError) as e:
        print(f"시작 실패: {e}", file=sys.stderr, flush=True)
        return 2
    srv = make_server(svc, env.get("MODEL_REGISTRY_HOST", "0.0.0.0"), int(env.get("MODEL_REGISTRY_PORT", 8080)),
                      threads=int(env.get("MODEL_REGISTRY_THREADS", 16)))
    # SIGTERM(컨테이너 종료)에도 진행 중 요청을 마친 뒤 멈춘다. shutdown은 serve_forever를 돌리는 스레드가 아닌 곳에서 불러야 한다.
    def _stop(signum, frame):
        # 신호 처리기는 메인 스레드의 어느 지점에서든 끼어들므로(출력 중일 수도 있다) 여기서는 출력하지 않고 스레드를 시작만 한다
        def _go():
            svc.log("종료 신호: 유휴 연결을 닫고 진행 중 요청을 마친 뒤 멈춘다")
            drain(srv, svc.shutdown_grace, svc.log)
        threading.Thread(target=_go, daemon=True).start()
    try:
        signal.signal(signal.SIGTERM, _stop)
    except ValueError:      # 메인 스레드가 아니면(시험) 건너뛴다
        pass
    print(f"model-registry 시작 포트 {srv.effective_port}", flush=True)     # 신호 처리기를 건 뒤에 알린다
    try:
        srv.run()
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()
        srv.task_dispatcher.shutdown()           # 작업 스레드를 멈춘다(drain이 응답을 다 보낸 뒤라 남은 일이 없다)
        shutil.rmtree(svc.tmp, ignore_errors=True)
    return 0
