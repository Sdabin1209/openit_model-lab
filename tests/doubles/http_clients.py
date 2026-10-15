"""HttpModelRegistry의 시험용 대역(HTTP.md I3). boundary_contracts.clients.HttpModelRegistry(v1.1.1)가 나오기 전에
서버 시험과 파이프라인의 원격 게시 시험에 쓴다. 해시는 받은 뒤 다시 계산한다(서버를 믿지 않는다)."""
from __future__ import annotations

import json
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from boundary_contracts import BoundaryError, canonical

from model_lab.service import tarutil

_CODES = {400: "INVALID_RECORD", 403: "PERMISSION_DENIED", 404: "NOT_FOUND", 409: "CONFLICT", 412: "FAILED_PRECONDITION",
          422: "INTEGRITY_ERROR", 503: "UNAVAILABLE"}


class HttpModelRegistry:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0):
        self.base, self.token, self.timeout = base_url.rstrip("/"), token, timeout

    def _req(self, method: str, path: str, body: bytes | None = None, ctype: str | None = None):
        r = urllib.request.Request(self.base + path, data=body, method=method)
        r.add_header("Authorization", f"Bearer {self.token}")
        if ctype:
            r.add_header("Content-Type", ctype)
        try:
            return urllib.request.urlopen(r, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                rec = json.loads(raw)
                raise BoundaryError.from_record(rec) from None
            except (ValueError, KeyError):
                raise BoundaryError(_CODES.get(e.code, "UNAVAILABLE"), f"HTTP {e.code}") from None
        except (urllib.error.URLError, OSError):
            raise BoundaryError("UNAVAILABLE", "서버에 연결할 수 없다") from None

    def _json(self, path: str):
        with self._req("GET", path) as r:
            return json.loads(r.read())

    def publish(self, manifest: dict, adapter_dir: Path, eval_report: bytes) -> dict:
        if not Path(adapter_dir).is_dir():
            raise BoundaryError("NOT_FOUND", "adapter_dir가 없다")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "manifest.json").write_bytes(json.dumps(manifest, ensure_ascii=False).encode())
            (td / "eval_report").write_bytes(eval_report)
            files = {"manifest.json": td / "manifest.json", "eval_report": td / "eval_report"}
            files.update(tarutil.tree_files(Path(adapter_dir), "adapter/"))
            tarutil.pack(files, td / "b.tar")
            with open(td / "b.tar", "rb") as f:
                data = f.read()
        with self._req("POST", "/v1/packages", data, "application/x-tar") as r:
            got = json.loads(r.read())
        if canonical.content_hash(got) != got.get("content_hash"):
            raise BoundaryError("INTEGRITY_ERROR", "응답의 content_hash가 맞지 않는다")
        return got

    def get_manifest(self, package_id: str, version: str) -> dict:
        m = self._json(f"/v1/packages/{urllib.parse.quote(package_id, safe='')}/versions/{urllib.parse.quote(version, safe='')}")
        if canonical.content_hash(m) != m.get("content_hash"):
            raise BoundaryError("INTEGRITY_ERROR", "content_hash가 맞지 않는다")
        return m

    def list_ids(self, *, cursor: Optional[str] = None, limit: int = 100) -> tuple:
        q = urllib.parse.urlencode({k: v for k, v in (("cursor", cursor), ("limit", limit)) if v is not None})
        r = self._json("/v1/packages?" + q)
        return r["items"], r["next_cursor"]

    def list_versions(self, package_id: str) -> list[str]:
        out, cur = [], None
        while True:
            q = "?cursor=" + cur if cur else ""
            r = self._json(f"/v1/packages/{urllib.parse.quote(package_id, safe='')}/versions{q}")
            out += r["items"]
            cur = r["next_cursor"]
            if not cur:
                return out

    def open_adapter(self, package_id: str, version: str, *, dest: Path) -> Path:
        m = self.get_manifest(package_id, version)
        out = Path(dest) / package_id / version
        if out.exists():
            shutil.rmtree(out)
        with tempfile.TemporaryDirectory() as td:
            tar = Path(td) / "a.tar"
            with self._req("GET", f"/v1/packages/{urllib.parse.quote(package_id, safe='')}/versions/{urllib.parse.quote(version, safe='')}/adapter") as r, open(tar, "wb") as f:
                shutil.copyfileobj(r, f)
            tarutil.unpack(tar, out, max_bytes=1 << 34)
        if canonical.directory_hash(out) != m["adapter_hash"]:
            shutil.rmtree(out, ignore_errors=True)
            raise BoundaryError("INTEGRITY_ERROR", "어댑터의 해시가 adapter_hash와 다르다")
        return out

    def get_eval_report(self, ref: str) -> bytes:
        with self._req("GET", f"/v1/eval-reports/{urllib.parse.quote(ref, safe='')}") as r:
            return r.read()
