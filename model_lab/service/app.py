"""model-registry 서비스(I3 서버). HTTP.md의 I3 표와 CONVENTIONS 9절을 따른다. 표준 라이브러리만 쓴다.

환경 변수(진입점 `python -m model_lab.service`가 읽는다):
  MODEL_REGISTRY_READ_TOKENS   역할=토큰,역할=토큰  (읽기 경로만)
  MODEL_REGISTRY_WRITE_TOKENS  model-lab=토큰         (쓰기 경로. 역할은 model-lab만 허용)
  MODEL_REGISTRY_ROOT          저장 볼륨(기본 /data)
  MODEL_REGISTRY_PORT          기본 8080 (0이면 빈 포트)
  MODEL_REGISTRY_MAX_BODY      게시 본문 최대 바이트(기본 4GiB)
로그는 표준 출력으로, 토큰과 본문은 남기지 않는다."""
from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from boundary_contracts import BoundaryError

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


class Service:
    def __init__(self, root: Path, read_tokens: dict, write_tokens: dict, *, max_body: int = 4 << 30,
                 log=lambda s: print(s, flush=True)):
        for role in write_tokens.values():
            if role != WRITE_ROLE:
                raise ValueError(f"쓰기 역할은 {WRITE_ROLE}만 허용한다")
        self.registry = FileModelRegistry(root)
        self.root, self.read, self.write, self.max_body, self.log = Path(root), read_tokens, write_tokens, max_body, log
        self.tmp = Path(root) / ".tmp"
        self.tmp.mkdir(parents=True, exist_ok=True)

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
        if method == "POST":
            if self._role(self.write, header) != WRITE_ROLE:
                raise BoundaryError("PERMISSION_DENIED", "쓰기 권한이 없다")
        else:
            if self._role(self.read, header) is None and self._role(self.write, header) is None:
                raise BoundaryError("PERMISSION_DENIED", "읽기 권한이 없다")

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

        def log_message(self, fmt, *args):   # 경로만(쿼리·토큰 제외)
            svc.log(f"{self.command} {urlparse(self.path).path} {args[1] if len(args) > 1 else ''}")

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype == "application/json" else ""))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode())

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
            self._json(STATUS[e.code], e.to_record())

        def _dispatch(self):
            u = urlparse(self.path)
            path, q = u.path, parse_qs(u.query)
            if path == "/healthz":
                return self._json(200, {"status": "ok"})
            if path == "/readyz":
                ok = svc.ready()
                return self._json(200 if ok else 503, {"status": "ready" if ok else "not-ready"})
            svc.authorize(self.command, self.headers.get("Authorization"))
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
                got = svc.registry.publish(manifest, ad, rp.read_bytes())
            self._json(200 if existed else 201, got)

        def _handle(self):
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
    srv = ThreadingHTTPServer((host, port), make_handler(svc))
    srv.daemon_threads = True
    return srv


def main(env=os.environ) -> int:
    try:
        read, write = parse_tokens(env.get("MODEL_REGISTRY_READ_TOKENS")), parse_tokens(env.get("MODEL_REGISTRY_WRITE_TOKENS"))
        if not read and not write:
            raise ValueError("MODEL_REGISTRY_READ_TOKENS 또는 MODEL_REGISTRY_WRITE_TOKENS가 필요하다")
        svc = Service(Path(env.get("MODEL_REGISTRY_ROOT", "/data")), read, write,
                      max_body=int(env.get("MODEL_REGISTRY_MAX_BODY", 4 << 30)))
    except (ValueError, OSError) as e:
        print(f"시작 실패: {e}", file=sys.stderr, flush=True)
        return 2
    srv = make_server(svc, env.get("MODEL_REGISTRY_HOST", "0.0.0.0"), int(env.get("MODEL_REGISTRY_PORT", 8080)))
    print(f"model-registry 시작 포트 {srv.server_address[1]}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
