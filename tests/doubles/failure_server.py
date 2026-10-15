"""HttpFailureStore용 작은 서버(HTTP.md I7 읽기 경로). 시험용 대역이며 ops-feedback의 구현이 아니다."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from boundary_contracts import BoundaryError

CODES = {"NOT_FOUND": 404, "PERMISSION_DENIED": 403, "UNAVAILABLE": 503, "INVALID_RECORD": 400}


class FailureServer:
    def __init__(self, cases: list[dict], traces: dict, token: str = "ft"):
        self.cases, self.traces, self.token, self.down = cases, traces, token, False
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def _send(self, code, obj):
                b = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

            def do_GET(self):
                try:
                    if outer.down:
                        raise BoundaryError("UNAVAILABLE", "시험: 다운")
                    if self.headers.get("Authorization") != f"Bearer {outer.token}":
                        raise BoundaryError("PERMISSION_DENIED", "토큰")
                    u = urlsplit(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
                    if u.path == "/v1/cases":
                        items = [c for c in outer.cases if (not q.get("review_status") or c["review_status"] == q["review_status"])]
                        if q.get("latest_only", "true") == "true":
                            latest = {}
                            for c in items:
                                if c["case_id"] not in latest or c["version"] > latest[c["case_id"]]["version"]:
                                    latest[c["case_id"]] = c
                            items = list(latest.values())
                        start = int(q.get("cursor") or 0); lim = int(q.get("limit", 100))
                        nxt = str(start + lim) if start + lim < len(items) else None
                        return self._send(200, {"items": items[start:start + lim], "next_cursor": nxt})
                    if u.path.startswith("/v1/deidentified-traces/"):
                        ref = unquote(u.path.rsplit("/", 1)[1])
                        if ref not in outer.traces:
                            raise BoundaryError("NOT_FOUND", "trace 없음")
                        return self._send(200, outer.traces[ref])
                    raise BoundaryError("NOT_FOUND", "경로 없음")
                except BoundaryError as e:
                    self._send(CODES[e.code], e.to_record())

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def url(self): return f"http://127.0.0.1:{self.srv.server_address[1]}"
    def stop(self): self.srv.shutdown()
