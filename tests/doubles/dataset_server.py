"""I2 DatasetStore HTTP 읽기 경로의 시험용 서버(HTTP.md `## I2`, D100). synthetic-data의 서버가 아니다. 쓰기(POST)는 만들지 않았다(model-lab은 읽기만 한다)."""
from __future__ import annotations

import io
import json
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from boundary_contracts import BoundaryError

CODES = {"NOT_FOUND": 404, "PERMISSION_DENIED": 403, "UNAVAILABLE": 503, "INVALID_RECORD": 400}


class DatasetServer:
    """store = DatasetStore Protocol(읽기). ids = 목록에 낼 데이터셋 ID."""

    def __init__(self, store, ids: list[str], token: str = "dt"):
        self.store, self.ids, self.token, self.calls = store, sorted(ids), token, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def _send(self, code, body: bytes, ctype="application/json"):
                self.send_response(code); self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

            def _json(self, code, obj): self._send(code, json.dumps(obj, ensure_ascii=False).encode())

            def do_GET(self):
                try:
                    if self.headers.get("Authorization") != f"Bearer {outer.token}":
                        raise BoundaryError("PERMISSION_DENIED", "토큰")
                    path = urlsplit(self.path).path
                    outer.calls.append(path)
                    parts = [unquote(p) for p in path.split("/")[1:]]       # v1 datasets <id> versions <v> [files [<path>]]
                    if parts[:2] != ["v1", "datasets"]:
                        raise BoundaryError("NOT_FOUND", "경로 없음")
                    if len(parts) == 2:
                        return self._json(200, {"items": outer.ids, "next_cursor": None})
                    did = parts[2]
                    if parts[3:4] != ["versions"]:
                        raise BoundaryError("NOT_FOUND", "경로 없음")
                    if len(parts) == 4:
                        return self._json(200, {"items": outer.store.list_versions(did), "next_cursor": None})
                    ver = parts[4]
                    if len(parts) == 5:
                        return self._json(200, outer.store.get_manifest(did, ver))
                    if parts[5] != "files":
                        raise BoundaryError("NOT_FOUND", "경로 없음")
                    if len(parts) == 6:
                        buf = io.BytesIO()
                        with tarfile.open(fileobj=buf, mode="w") as tf:
                            for f in outer.store.get_manifest(did, ver)["files"]:
                                with outer.store.open_file(did, ver, f["path"]) as src:
                                    data = src.read()
                                ti = tarfile.TarInfo(f["path"]); ti.size = len(data)
                                tf.addfile(ti, io.BytesIO(data))
                        return self._send(200, buf.getvalue(), "application/x-tar")
                    with outer.store.open_file(did, ver, parts[6]) as src:
                        return self._send(200, src.read(), "application/octet-stream")
                except BoundaryError as e:
                    self._json(CODES.get(e.code, 500), e.to_record())

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def url(self): return f"http://127.0.0.1:{self.srv.server_address[1]}"
    def stop(self): self.srv.shutdown()
