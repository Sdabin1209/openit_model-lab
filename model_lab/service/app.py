"""model-registry 서비스(I3 서버). HTTP.md의 I3 표와 CONVENTIONS 9절을 따른다. WSGI(PEP 3333) 앱을 waitress로 서빙한다(ADR 0023).

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

import hmac
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import parse_qs

import waitress
from boundary_contracts import BoundaryError, SecretError, read_secret

from ..registry import FileModelRegistry
from . import tarutil

STATUS = {"INVALID_RECORD": 400, "PERMISSION_DENIED": 403, "NOT_FOUND": 404, "CONFLICT": 409,
          "FAILED_PRECONDITION": 412, "UNSUPPORTED_VERSION": 422, "INTEGRITY_ERROR": 422, "UNAVAILABLE": 503}
WRITE_ROLE = "model-lab"


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


class Unauthenticated(BoundaryError):
    """토큰이 없거나 틀렸다 -> 401 + `WWW-Authenticate: Bearer`. code는 PERMISSION_DENIED 그대로다(CONVENTIONS.md 6절, D106).
    토큰은 맞지만 역할이 모자란 경우는 이 클래스가 아니라 일반 PERMISSION_DENIED -> 403이다."""


class Service:
    def __init__(self, root: Path, read_tokens: dict, write_tokens: dict, *, max_body: int = 4 << 30,
                 log=lambda s: print(s, flush=True), socket_timeout: float = 30.0, max_connections: int = 32,
                 max_publishes: int = 1, auth_fail_limit: int = 10, auth_fail_window: float = 60.0,
                 min_free_gb: float = 1.0, free_bytes=None, shutdown_grace: float = 8.0):
        for role in write_tokens.values():
            if role != WRITE_ROLE:
                raise ValueError(f"쓰기 역할은 {WRITE_ROLE}만 허용한다")
        self.registry = FileModelRegistry(root, link_on_commit=True)    # 풀어 둔 임시 폴더에서 하드 링크로 올려 디스크를 아낀다
        self.root, self.read, self.write, self.max_body, self.log = Path(root), read_tokens, write_tokens, max_body, log
        self.tmp = Path(root) / ".tmp"
        self.socket_timeout, self.max_connections = socket_timeout, max_connections
        self.auth_fail_limit, self.auth_fail_window, self.min_free_gb = auth_fail_limit, auth_fail_window, min_free_gb
        self.shutdown_grace = shutdown_grace
        self._free_bytes = free_bytes or (lambda: shutil.disk_usage(self.root).free)    # 시험에서 주입한다
        self.publish_slots = threading.BoundedSemaphore(max(1, max_publishes))
        self._fails: dict = defaultdict(deque)
        self._fails_mu = threading.Lock()
        self.startup_cleanup()

    def startup_cleanup(self) -> None:
        """재시작 후 정상화(ML-3): 이전 실행이 남긴 요청 임시 폴더와 게시 중단 잔해를 지운다. 서버가 시작될 때 한 번만 부른다."""
        if self.tmp.exists():
            shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp.mkdir(parents=True, exist_ok=True)
        n = self.registry.recover()
        if n:
            self.log(f"시작 정리: 게시 중단 잔해 {n}개를 지웠다")

    # ---- 자원 한도(ML-1, ML-2) ----
    def check_free_space(self, body_bytes: int) -> None:
        """본문(.tmp) + 풀어 둔 사본이 들어갈 여유(본문의 2배 + min_free_gb)가 없으면 받기 전에 거절한다(FAULT_POLICY 13항: 503)."""
        need = 2 * body_bytes + int(self.min_free_gb * 2**30)
        if self._free_bytes() < need:
            self.log(f"경보: 디스크 여유 부족으로 게시 거절(필요 {need >> 20}MiB, 여유 {self._free_bytes() >> 20}MiB)")
            raise BoundaryError("UNAVAILABLE", "저장소 여유 공간이 부족하다")

    def token_valid(self, header: str | None) -> bool:
        """읽기 또는 쓰기 토큰으로 인식되는가(역할이 모자란 것과 구분한다: 유효한 토큰의 403은 실패 횟수에 넣지 않는다)."""
        return self._role(self.read, header) is not None or self._role(self.write, header) is not None

    # 실패 제한의 의미(ADR 0009): 틀린 토큰이 쏟아질 때 로그 스팸과 CPU(토큰 비교 반복)를 줄이는 장치이지 무차별 대입 방어가 아니다.
    # 제한된 주소에서도 올바른 토큰은 통과하므로(같은 주소의 정상 클라이언트 보호) 추측이 맞으면 막지 못한다. 그 방어는 토큰 엔트로피(MIN_TOKEN_LEN)다.
    def auth_blocked(self, addr: str) -> bool:
        now = time.monotonic()
        with self._fails_mu:
            q = self._fails[addr]
            while q and now - q[0] > self.auth_fail_window:
                q.popleft()
            if not q:
                self._fails.pop(addr, None)
            return len(q) >= self.auth_fail_limit

    def note_auth_failure(self, addr: str) -> None:
        with self._fails_mu:
            self._fails[addr].append(time.monotonic())

    # ---- 인증 ----
    def _role(self, tokens: dict, header: str | None) -> str | None:
        if not header or not header.startswith("Bearer "):
            return None
        given = header[7:].strip().encode()
        found = None
        for tok, role in tokens.items():          # 시간 일정 비교
            if hmac.compare_digest(tok.encode(), given):
                found = role
        return found

    def authorize(self, method: str, header: str | None) -> None:
        # 왜: 401(인증 안 됨)과 403(인증됐으나 권한 없음)을 가른다. RFC 9110 15.5.2·11.6.1, RFC 6750 3절, CONVENTIONS.md 6절
        if not self.token_valid(header):
            raise Unauthenticated("PERMISSION_DENIED", "토큰이 없거나 올바르지 않다")
        if method == "POST":
            if self._role(self.write, header) != WRITE_ROLE:
                raise BoundaryError("PERMISSION_DENIED", "쓰기 권한이 없다")

    def ready(self) -> bool:
        try:
            self.registry.list_ids(limit=1)
            probe = self.tmp / ".ready"
            probe.write_text("1")
            probe.unlink()
            return True
        except Exception:
            return False


_PKG = re.compile(r"^/v1/packages/([^/]+)/versions/([^/]+)(/adapter)?$")
_VERS = re.compile(r"^/v1/packages/([^/]+)/versions$")
_REPORT = re.compile(r"^/v1/eval-reports/([^/]+)$")
_REASON = {200: "OK", 201: "Created", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 409: "Conflict",
           412: "Precondition Failed", 422: "Unprocessable Entity", 503: "Service Unavailable"}


class _Closing:
    """응답 본문 이터러블. WSGI 서버가 다 보낸 뒤(또는 연결이 끊겨도) close()를 불러 임시 폴더를 지운다."""

    def __init__(self, path: Path, cleanup):
        self._f, self._cleanup = open(path, "rb"), cleanup

    def __iter__(self):
        while chunk := self._f.read(1 << 20):
            yield chunk

    def close(self) -> None:
        self._f.close()
        self._cleanup()


def make_app(svc: Service):
    """WSGI 앱. 연결 상한·timeout·본문 상한·스레드는 서버(waitress) 설정이 맡고(ADR 0023), 여기는 라우팅과 인증, 게시만 한다."""

    def app(environ, start_response):
        method, path = environ["REQUEST_METHOD"], environ["PATH_INFO"]
        # PATH_INFO는 이미 퍼센트 해제되어 있다. 이름에 '/'가 없다는 ID 규칙(CONVENTIONS 2절)이라 해제 뒤 분할해도 안전하다.
        status, headers, out = 500, [], b""
        try:
            status, headers, out = _dispatch(environ, method, path)
        except BoundaryError as e:
            status, headers, out = _error(e)
        except Exception as e:   # 내부 오류는 원문 없이 503으로 알리고 로그에는 형식만 남긴다
            svc.log(f"내부 오류 {type(e).__name__}")
            status, headers, out = _error(BoundaryError("UNAVAILABLE", "서비스 내부 오류"))
        svc.log(f"{method} {path} {status}")      # 경로만(쿼리·토큰 제외)
        start_response(f"{status} {_REASON.get(status, 'Error')}", headers)
        return out

    def _json(code: int, obj, headers=()):
        b = json.dumps(obj, ensure_ascii=False).encode()
        return code, [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(b))), *headers], [b]

    def _error(e: BoundaryError):
        if isinstance(e, Unauthenticated):
            return _json(401, e.to_record(), [("WWW-Authenticate", "Bearer")])
        return _json(STATUS[e.code], e.to_record())

    def _bytes(code: int, b: bytes, ctype: str):
        return code, [("Content-Type", ctype), ("Content-Length", str(len(b)))], [b]

    def _dispatch(environ, method: str, path: str):
        q = parse_qs(environ.get("QUERY_STRING", ""))
        if path == "/healthz":
            return _json(200, {"status": "ok"})
        if path == "/readyz":
            ok = svc.ready()
            return _json(200 if ok else 503, {"status": "ready" if ok else "not-ready"})
        addr = environ.get("REMOTE_ADDR", "")
        try:
            svc.authorize(method, environ.get("HTTP_AUTHORIZATION"))      # 토큰 검증이 먼저다: 유효한 토큰은 제한과 무관하게 통과한다
        except BoundaryError as e:
            if isinstance(e, Unauthenticated):
                # 제한은 유효하지 않은(없거나 틀린) 토큰을 보낸 요청에만 건다. 프록시·Docker 뒤에서는 호출자가 모두 같은 주소로
                # 보이므로 주소만으로 막으면 정상 호출자까지 막힌다(ML-1 재점검).
                throttled = svc.auth_blocked(addr)
                svc.note_auth_failure(addr)
                if throttled:
                    raise BoundaryError("UNAVAILABLE", "인증 시도가 너무 많다. 잠시 뒤 다시 시도한다") from None
            raise
        if method == "POST":
            if path != "/v1/packages":
                raise BoundaryError("NOT_FOUND", "경로가 없다")
            return _publish(environ, addr)
        if method != "GET":
            raise BoundaryError("NOT_FOUND", "경로가 없다")
        if path == "/v1/packages":
            items, nxt = svc.registry.list_ids(cursor=_one(q, "cursor"), limit=_limit(q))
            return _json(200, {"items": items, "next_cursor": nxt})
        m = _VERS.match(path)
        if m:
            vs = svc.registry.list_versions(m.group(1))
            items, nxt = FileModelRegistry._page(vs, _one(q, "cursor"), _limit(q))
            return _json(200, {"items": items, "next_cursor": nxt})
        m = _PKG.match(path)
        if m:
            pid, ver = m.group(1), m.group(2)
            if not m.group(3):
                return _json(200, svc.registry.get_manifest(pid, ver))
            td = tempfile.TemporaryDirectory(dir=svc.tmp)
            try:
                ad = svc.registry.open_adapter(pid, ver, dest=Path(td.name) / "x")      # 해시를 검증한 뒤에만 낸다
                tar = Path(td.name) / "adapter.tar"
                tarutil.pack(tarutil.tree_files(ad), tar)
                body = _Closing(tar, td.cleanup)
            except BaseException:
                td.cleanup()
                raise
            return 200, [("Content-Type", "application/x-tar"), ("Content-Length", str(tar.stat().st_size))], body
        m = _REPORT.match(path)
        if m:
            return _bytes(200, svc.registry.get_eval_report(m.group(1)), "application/octet-stream")
        raise BoundaryError("NOT_FOUND", "경로가 없다")

    def _publish(environ, addr: str):
        # 본문 크기 상한은 서버(max_request_body_size)가 이미 지켰다. waitress는 본문을 다 받은 뒤 앱을 부른다(ADR 0023).
        n = int(environ.get("CONTENT_LENGTH") or -1)
        if n < 0:
            raise BoundaryError("INVALID_RECORD", "Content-Length가 필요하다")
        if not svc.publish_slots.acquire(blocking=False):      # 동시 게시 상한(ML-1): 기다리게 하지 않고 재시도하게 한다
            raise BoundaryError("UNAVAILABLE", "다른 게시가 진행 중이다. 잠시 뒤 다시 시도한다")
        try:
            return _publish_locked(environ, n, addr)
        finally:
            svc.publish_slots.release()

    def _publish_locked(environ, n: int, addr: str):
        svc.check_free_space(n)
        with tempfile.TemporaryDirectory(dir=svc.tmp) as td:
            td = Path(td)
            tarutil.unpack(environ["wsgi.input"], td / "in", max_bytes=svc.max_body)
            mp, rp, ad = td / "in" / "manifest.json", td / "in" / "eval_report", td / "in" / "adapter"
            if not mp.is_file() or not rp.is_file():
                raise BoundaryError("INVALID_RECORD", "tar에 manifest.json과 eval_report가 있어야 한다")
            try:
                manifest = json.loads(mp.read_text(encoding="utf-8"))
            except ValueError:
                raise BoundaryError("INVALID_RECORD", "manifest.json이 JSON이 아니다") from None
            if not isinstance(manifest, dict):
                raise BoundaryError("INVALID_RECORD", "manifest.json이 객체가 아니다")
            ad.mkdir(exist_ok=True)
            existed = True
            try:
                svc.registry.get_manifest(str(manifest.get("package_id")), str(manifest.get("version")))
            except BoundaryError as e:
                if e.code != "NOT_FOUND":
                    raise
                existed = False
            got = svc.registry.publish(manifest, ad, rp.read_bytes(), publisher={
                "role": WRITE_ROLE, "remote": addr, "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return _json(200 if existed else 201, got)

    return app


def _one(q: dict, k: str):
    return q[k][0] if k in q else None


def _limit(q: dict) -> int:
    try:
        v = int(_one(q, "limit") or 100)
    except ValueError:
        raise BoundaryError("INVALID_RECORD", "limit이 올바르지 않다") from None
    if v < 1:
        raise BoundaryError("INVALID_RECORD", "limit은 1 이상이어야 한다")
    return min(v, 500)


def make_server(svc: Service, host: str = "127.0.0.1", port: int = 0, threads: int = 16):
    """waitress 서버. 값의 출처와 한계는 ADR 0023. 호출자가 srv.run()으로 돌리고 srv.close()로 닫는다(`effective_port`가 실제 포트)."""
    return waitress.create_server(
        make_app(svc), host=host, port=port, threads=threads, ident="model-registry",
        connection_limit=svc.max_connections + 2,      # 왜 +2: waitress는 리스너와 트리거(내부 소켓)도 연결 수에 센다. max_connections=1이면 하나도 받지 못하는 것을 시험으로 확인했다
        channel_timeout=max(1, int(svc.socket_timeout)),
        cleanup_interval=max(1, min(5, int(svc.socket_timeout))),      # 유휴 연결 점검 주기. timeout을 이 값만큼만 넘겨 닫는다
        max_request_body_size=svc.max_body,
        clear_untrusted_proxy_headers=True)


def drain(srv, grace: float, log=lambda s: None) -> None:
    """우아한 종료(ML-2): 새 연결을 받지 않고, 유휴 연결은 보낼 것을 보낸 뒤 닫고, 요청이 진행 중인 연결(받는 중 포함)은 grace초까지 기다린다.
    남은 연결은 grace 뒤 끊는다. waitress에는 이 기능이 없어(SIGINT면 작업 스레드만 멈추고 응답을 흘려보내지 못한다) 서버의 채널 목록으로 한다."""
    srv.accepting = False                                  # readable()이 False를 내 accept를 멈춘다. 이미 backlog에 있는 연결은 종료와 함께 끊긴다
    deadline = time.monotonic() + grace
    closed = 0
    while True:
        busy = 0
        for ch in list(srv._map.values()):
            if not hasattr(ch, "requests"):                 # 리스너·트리거는 채널이 아니다
                continue
            if ch.requests or ch.request is not None:
                busy += 1
            elif not ch.close_when_flushed:
                ch.close_when_flushed = True                # 이미 만든 응답은 보낸 뒤 닫는다
                closed += 1
        srv.pull_trigger()
        if not busy or time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    for ch in list(srv._map.values()):
        if hasattr(ch, "requests"):
            ch.will_close = True                            # 유예가 지나 남은 연결을 끊는다
    srv.pull_trigger()
    log(f"종료: 유휴 연결 {closed}개를 닫았고 진행 중 {busy}개는 " + ("끊었다" if busy else "없었다"))
    # 채널이 닫혀 사라지길 잠시 기다린 뒤 리스너와 트리거를 맵에서 뺀다. 맵이 비면 srv.run()의 루프가 끝난다.
    # 소켓을 닫는 일은 루프가 끝난 뒤 srv.close()가 한다: 루프가 select 중인 fd를 다른 스레드에서 닫으면 EBADF로 루프가 죽는다.
    stop_by = time.monotonic() + 2.0
    while time.monotonic() < stop_by and any(hasattr(ch, "requests") for ch in list(srv._map.values())):
        time.sleep(0.02)
    srv.del_channel()
    srv.trigger.del_channel()


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
