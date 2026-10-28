"""model-registry 서비스(I3 서버). HTTP.md의 I3 표와 CONVENTIONS 9절을 따른다. 표준 라이브러리만 쓴다.

환경 변수(진입점 `python -m model_lab.service`가 읽는다). 비밀 두 개는 boundary_contracts.read_secret(D84)으로 읽는다:
  MODEL_REGISTRY_READ_TOKENS_FILE / MODEL_REGISTRY_READ_TOKENS    역할=토큰,역할=토큰  (읽기 경로만)
  MODEL_REGISTRY_WRITE_TOKENS_FILE / MODEL_REGISTRY_WRITE_TOKENS  model-lab=토큰         (쓰기 경로. 역할은 model-lab만 허용)
  (NAME_FILE이 있으면 그 파일 내용, 없으면 NAME 환경 변수. 운영은 파일, 환경 변수는 개발용)
  MODEL_REGISTRY_ROOT          저장 볼륨(기본 /data)
  MODEL_REGISTRY_PORT          기본 8080 (0이면 빈 포트)
  MODEL_REGISTRY_MAX_BODY      게시 본문 최대 바이트(기본 4GiB)
  MODEL_REGISTRY_SOCKET_TIMEOUT  연결 하나의 소켓 읽기/쓰기 timeout 초(기본 30; 느린 클라이언트가 스레드를 잡지 못하게 한다, ML-1)
  MODEL_REGISTRY_MAX_CONNECTIONS 동시 연결 상한(기본 32; 넘으면 503 UNAVAILABLE)
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
import socket
import sys
import tempfile
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from boundary_contracts import BoundaryError, SecretError, read_secret

from ..registry import FileModelRegistry
from . import tarutil

STATUS = {"INVALID_RECORD": 400, "PERMISSION_DENIED": 403, "NOT_FOUND": 404, "CONFLICT": 409,
          "FAILED_PRECONDITION": 412, "UNSUPPORTED_VERSION": 422, "INTEGRITY_ERROR": 422, "UNAVAILABLE": 503}
WRITE_ROLE = "model-lab"
DRAIN_IDLE = 0.25        # 거절 전 본문을 버릴 때 다음 조각을 기다리는 최대 초. 근거 기록 없음(측정 안 함)
DRAIN_CAP = 1 << 20      # 거절 응답 전에 버리는 요청 본문의 최대 바이트. 1MiB는 근거 기록 없음(측정 안 함), ADR 0007


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


def make_handler(svc: Service):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "model-registry"
        timeout = svc.socket_timeout           # StreamRequestHandler가 연결 소켓에 건다(느린 클라이언트 차단, ML-1)

        _sent_close = False

        def setup(self):
            super().setup()
            self.server.track(self.request)

        def finish(self):
            try:
                super().finish()
            finally:
                self.server.untrack(self.request)

        def end_headers(self):
            # 종료 중이면 모든 응답에 Connection: close를 달아 클라이언트가 연결을 재사용하지 않게 한다(ML-2 재점검)
            if (self.server.draining or self.close_connection) and not self._sent_close:
                self._sent_close = True
                self.send_header("Connection", "close")
            super().end_headers()

        def log_message(self, fmt, *args):   # 경로만(쿼리·토큰 제외)
            svc.log(f"{self.command} {urlparse(self.path).path} {args[1] if len(args) > 1 else ''}")

        def _send(self, code: int, body: bytes, ctype: str = "application/json", headers: dict | None = None) -> None:
            self.send_response(code)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype == "application/json" else ""))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, obj, headers: dict | None = None) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), headers=headers)

        def _file(self, path: Path, ctype: str) -> None:
            size = path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(path, "rb") as f:
                shutil.copyfileobj(f, self.wfile)

        def _fail(self, e: BoundaryError) -> None:
            if self.command in ("POST", "PUT", "PATCH"):
                self.close_connection = True      # 본문을 다 읽지 않았을 수 있다
                self._drain()
            if isinstance(e, Unauthenticated):
                return self._json(401, e.to_record(), {"WWW-Authenticate": "Bearer"})
            self._json(STATUS[e.code], e.to_record())

        _consumed = False

        def _drain(self) -> None:
            """오류로 답하기 전에 요청 본문을 버리면서 읽는다. 읽지 않고 답하면 아직 보내는 중인 클라이언트가 연결 초기화로
            UNAVAILABLE을 받아 403/400 같은 실제 오류를 못 본다(간헐 실패의 원인이었다)."""
            if self._consumed:
                return
            self._consumed = True
            try:
                # 거절 때 본문을 끝까지 읽지 않는다(최대 4GiB를 읽게 하면 거절이 자원 소모 경로가 된다). 작은 본문만 버려서
                # 아직 보내는 클라이언트가 연결 초기화 대신 실제 오류를 보게 하고, 나머지는 연결을 닫는다(Connection: close).
                left = min(int(self.headers.get("Content-Length") or 0), DRAIN_CAP)
                self.connection.settimeout(DRAIN_IDLE)      # 클라이언트가 본문을 보내지 않으면 기다리지 않는다(느린 송신자가 스레드를 잡지 못하게)
                while left > 0:
                    chunk = self.rfile.read1(min(1 << 20, left)) if hasattr(self.rfile, "read1") else self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    left -= len(chunk)
            except (OSError, ValueError):
                pass

        def _dispatch(self):
            u = urlparse(self.path)
            path, q = u.path, parse_qs(u.query)
            if path == "/healthz":
                return self._json(200, {"status": "ok"})
            if path == "/readyz":
                ok = svc.ready()
                return self._json(200 if ok else 503, {"status": "ready" if ok else "not-ready"})
            addr = self.client_address[0]
            hdr = self.headers.get("Authorization")
            try:
                svc.authorize(self.command, hdr)             # 토큰 검증이 먼저다: 유효한 토큰은 제한과 무관하게 통과한다
            except BoundaryError as e:
                if isinstance(e, Unauthenticated):
                    # 제한은 유효하지 않은(없거나 틀린) 토큰을 보낸 요청에만 건다. 프록시·Docker 뒤에서는 호출자가 모두 같은 주소로
                    # 보이므로 주소만으로 막으면 정상 호출자까지 막힌다(ML-1 재점검).
                    throttled = svc.auth_blocked(addr)
                    svc.note_auth_failure(addr)
                    if throttled:
                        raise BoundaryError("UNAVAILABLE", "인증 시도가 너무 많다. 잠시 뒤 다시 시도한다") from None
                raise
            if self.command == "POST":
                if path != "/v1/packages":
                    raise BoundaryError("NOT_FOUND", "경로가 없다")
                return self._publish()
            if self.command != "GET":
                raise BoundaryError("NOT_FOUND", "경로가 없다")
            if path == "/v1/packages":
                items, nxt = svc.registry.list_ids(cursor=_one(q, "cursor"), limit=_limit(q))
                return self._json(200, {"items": items, "next_cursor": nxt})
            m = _VERS.match(path)
            if m:
                vs = svc.registry.list_versions(unquote(m.group(1)))
                items, nxt = FileModelRegistry._page(vs, _one(q, "cursor"), _limit(q))
                return self._json(200, {"items": items, "next_cursor": nxt})
            m = _PKG.match(path)
            if m:
                pid, ver = unquote(m.group(1)), unquote(m.group(2))
                if not m.group(3):
                    return self._json(200, svc.registry.get_manifest(pid, ver))
                with tempfile.TemporaryDirectory(dir=svc.tmp) as td:
                    ad = svc.registry.open_adapter(pid, ver, dest=Path(td) / "x")      # 해시를 검증한 뒤에만 낸다
                    tar = Path(td) / "adapter.tar"
                    tarutil.pack(tarutil.tree_files(ad), tar)
                    return self._file(tar, "application/x-tar")
            m = _REPORT.match(path)
            if m:
                return self._send(200, svc.registry.get_eval_report(unquote(m.group(1))), "application/octet-stream")
            raise BoundaryError("NOT_FOUND", "경로가 없다")

        def _publish(self) -> None:
            n = int(self.headers.get("Content-Length") or -1)
            if n < 0:
                raise BoundaryError("INVALID_RECORD", "Content-Length가 필요하다")
            if n > svc.max_body:
                raise BoundaryError("INVALID_RECORD", "본문이 너무 크다")
            if not svc.publish_slots.acquire(blocking=False):      # 동시 게시 상한(ML-1): 기다리게 하지 않고 재시도하게 한다
                raise BoundaryError("UNAVAILABLE", "다른 게시가 진행 중이다. 잠시 뒤 다시 시도한다")
            try:
                self._publish_locked(n)
            finally:
                svc.publish_slots.release()

        def _publish_locked(self, n: int) -> None:
            svc.check_free_space(n)
            self._consumed = True            # 여기부터 이 함수가 본문을 읽는다. 이전 단계의 거절은 _fail이 제한된 만큼만 버리고 연결을 닫는다
            with tempfile.TemporaryDirectory(dir=svc.tmp) as td:
                td = Path(td)
                body = td / "body.tar"
                left = n
                with open(body, "wb") as f:
                    while left:
                        chunk = self.rfile.read(min(1 << 20, left))
                        if not chunk:
                            raise BoundaryError("INVALID_RECORD", "본문이 중간에 끊겼다")
                        f.write(chunk); left -= len(chunk)
                tarutil.unpack(body, td / "in", max_bytes=svc.max_body)
                body.unlink()                      # 풀었으면 본문 tar는 더 필요 없다(최대 사용량을 줄인다, ML-2)
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
                    "role": WRITE_ROLE, "remote": self.client_address[0],
                    "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            self._json(200 if existed else 201, got)

        def _handle(self):
            # 핸들러 인스턴스는 keep-alive 연결 하나가 요청 여러 개에 재사용한다. 요청별 상태를 되돌리지 않으면 앞 요청의 _consumed=True가
            # 남아 다음 거절에서 본문을 버리지 않는다(연결 초기화 위험).
            self._consumed = False
            self._sent_close = False
            self.server.set_busy(self.request, True)
            try:
                self._handle_inner()
            finally:
                self.server.set_busy(self.request, False)
                if self.server.draining:
                    self.close_connection = True

        def _handle_inner(self):
            try:
                self._dispatch()
            except BoundaryError as e:
                self._fail(e)
            except Exception as e:   # 내부 오류는 원문 없이 UNAVAILABLE이 아니라 재시도 불가 오류로? -> 503으로 알리고 로그에는 형식만
                svc.log(f"내부 오류 {type(e).__name__}")
                self._fail(BoundaryError("UNAVAILABLE", "서비스 내부 오류"))

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _handle

    return H


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


def make_server(svc: Service, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    srv = _Server((host, port), make_handler(svc), svc.max_connections)
    return srv


class _Server(ThreadingHTTPServer):
    """동시 연결 상한(ML-1)과 우아한 종료(ML-3). 진행 중 요청을 끊지 않도록 스레드는 daemon이 아니고 server_close가 기다린다."""
    daemon_threads = False
    block_on_close = True

    def __init__(self, addr, handler, max_connections: int):
        super().__init__(addr, handler)
        self._slots = threading.BoundedSemaphore(max(1, max_connections))
        self.draining = False
        self._conns: dict = {}                  # 연결 소켓 -> 요청을 처리 중인가
        self._conns_mu = threading.Lock()

    def track(self, sock) -> None:
        with self._conns_mu:
            self._conns[sock] = False

    def untrack(self, sock) -> None:
        with self._conns_mu:
            self._conns.pop(sock, None)

    def set_busy(self, sock, busy: bool) -> None:
        with self._conns_mu:
            if sock in self._conns:
                self._conns[sock] = busy

    def _close_conns(self, only_idle: bool) -> int:
        with self._conns_mu:
            targets = [c for c, busy in self._conns.items() if not (only_idle and busy)]
        for c in targets:
            try:
                c.shutdown(socket.SHUT_RDWR)    # 읽기 대기 중인 핸들러 스레드가 즉시 깨어나 끝난다
            except OSError:
                pass
        return len(targets)

    def drain(self, grace: float, log=lambda s: None) -> None:
        """우아한 종료(ML-2): 새 응답은 Connection: close, 유휴 keep-alive 연결은 즉시 닫고, 진행 중 요청만 grace초 기다린 뒤 남은 연결을 끊는다.
        accept 루프는 호출자가 shutdown()으로 멈춘다."""
        self.draining = True
        n = self._close_conns(only_idle=True)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            with self._conns_mu:
                if not any(self._conns.values()):
                    break
            time.sleep(0.05)
        left = self._close_conns(only_idle=False)
        log(f"종료: 유휴 연결 {n}개를 닫았고 유예 시간 뒤 진행 중 {left}개를 끊었다" if left else f"종료: 유휴 연결 {n}개를 닫았다")

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.settimeout(2)
                body = json.dumps(BoundaryError("UNAVAILABLE", "연결이 너무 많다").to_record(), ensure_ascii=False).encode()
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\nContent-Length: "
                                + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


# 아래 기본값의 근거(ADR 0007): 모두 측정하지 않은 보수적 시작값이다. 조정은 환경 변수로 한다. 근거 출처 없음 = 측정 안 함.
#  소켓 timeout 30초: 정상 클라이언트는 1MiB 조각을 이어 보내므로 한 번의 읽기 대기가 길지 않다는 가정(측정 안 함); 느린 클라이언트가 스레드를 잡는 시간의 상한.
#  동시 연결 32: 소비자가 release-gate 등 소수이고 게시는 1건뿐이라는 가정(측정 안 함); 스레드 서버의 자원 상한.
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
    srv = make_server(svc, env.get("MODEL_REGISTRY_HOST", "0.0.0.0"), int(env.get("MODEL_REGISTRY_PORT", 8080)))
    # SIGTERM(컨테이너 종료)에도 진행 중 요청을 마친 뒤 멈춘다. shutdown은 serve_forever를 돌리는 스레드가 아닌 곳에서 불러야 한다.
    def _stop(signum, frame):
        # 신호 처리기는 메인 스레드의 어느 지점에서든 끼어들므로(출력 중일 수도 있다) 여기서는 출력하지 않고 스레드를 시작만 한다
        def _go():
            svc.log("종료 신호: 유휴 연결을 닫고 진행 중 요청을 마친 뒤 멈춘다")
            threading.Thread(target=srv.shutdown, daemon=True).start()    # accept 루프 정지
            srv.drain(svc.shutdown_grace, svc.log)
        threading.Thread(target=_go, daemon=True).start()
    try:
        signal.signal(signal.SIGTERM, _stop)
    except ValueError:      # 메인 스레드가 아니면(시험) 건너뛴다
        pass
    print(f"model-registry 시작 포트 {srv.server_address[1]}", flush=True)     # 신호 처리기를 건 뒤에 알린다
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()                       # 진행 중 요청 스레드를 기다린다(block_on_close)
        shutil.rmtree(svc.tmp, ignore_errors=True)
    return 0
