"""I3 ModelRegistry(이 영역이 소유). 파일 구현과 메모리 구현이 같은 규칙 코드를 쓴다.

규칙(CONVENTIONS 3~5절, INTERFACES I3):
- 쓰기 전에 ModelPackageManifest 스키마·content_hash·adapter_hash·시스템 프롬프트 파일(D75)을 확인한다. 어긋나면 INVALID_RECORD/INTEGRITY_ERROR.
- 같은 (package_id, version)과 같은 content_hash면 기존 것을 돌려준다. 다르면 CONFLICT. 불변이다.
- list_ids는 처음 게시된 순서, list_versions는 오래된 것부터다. 새 항목은 뒤에 붙는다.
- open_adapter는 풀고 나서 해시를 다시 계산한다. 다르면 INTEGRITY_ERROR.
- eval_report는 manifest.eval_report_ref 이름으로 저장한다. 같은 ref에 다른 바이트면 CONFLICT.
"""
from __future__ import annotations

import copy
import fcntl
import json
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Optional

from boundary_contracts import BoundaryError, canonical, validator

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def _check_id(value: str, what: str) -> None:
    if not isinstance(value, str) or len(value) > 128 or not _ID_RE.match(value):
        raise BoundaryError("INVALID_RECORD", f"{what}가 Id 규칙을 어겼다")


def _copy_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if p.is_dir():
            (dst / rel).mkdir(parents=True, exist_ok=True)
        elif p.is_file():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dst / rel)


def verify_system_prompt(adapter_dir: Path, ref: dict) -> None:
    """어댑터 디렉터리 안에 시스템 프롬프트 파일이 있고 해시가 sha256과 같아야 한다(D75, 어기면 INTEGRITY_ERROR)."""
    root = Path(adapter_dir).resolve()
    f = (root / ref["path"]).resolve()
    if root not in f.parents or not f.is_file() or canonical.file_hash(f) != ref["sha256"]:
        raise BoundaryError("INTEGRITY_ERROR", "시스템 프롬프트 파일이 없거나 system_prompt.sha256과 다르다")


class _Core:
    """저장 방식과 무관한 규칙. 하위 클래스는 _Storage 동작(아래 _s_*)만 구현한다."""

    def __init__(self) -> None:
        self._validate = validator("ModelPackageManifest").validate

    # --- 저장소 동작(하위 클래스) ---
    def _s_get_manifest(self, pid: str, ver: str) -> Optional[dict]: raise NotImplementedError
    def _s_get_report(self, ref: str) -> Optional[bytes]: raise NotImplementedError
    def _s_commit(self, manifest: dict, adapter_dir: Path, ref: str, report: bytes) -> None: raise NotImplementedError
    def _s_ids(self) -> list[str]: raise NotImplementedError
    def _s_versions(self, pid: str) -> list[str]: raise NotImplementedError
    def _s_extract(self, pid: str, ver: str, dest: Path) -> None: raise NotImplementedError
    def _lock(self): raise NotImplementedError

    # --- I3 ---
    def publish(self, manifest: dict, adapter_dir: Path, eval_report: bytes) -> dict:
        try:
            self._validate(manifest)
        except Exception as e:  # jsonschema.ValidationError
            path = "/" + "/".join(map(str, getattr(e, "absolute_path", [])))
            raise BoundaryError("INVALID_RECORD", f"ModelPackageManifest 위반: {path}") from None
        pid, ver = manifest["package_id"], manifest["version"]
        _check_id(ver, "version")
        _check_id(manifest["eval_report_ref"], "eval_report_ref")
        try:
            if canonical.content_hash(manifest) != manifest["content_hash"]:
                raise BoundaryError("INTEGRITY_ERROR", "content_hash가 레코드와 맞지 않는다")
            if canonical.sha256_hash(manifest["serving_config"]) != manifest["serving_config_hash"]:
                raise BoundaryError("INVALID_RECORD", "serving_config_hash가 serving_config와 맞지 않는다")
        except ValueError as e:
            raise BoundaryError("INVALID_RECORD", f"해시를 계산할 수 없다: {e}") from None
        if not isinstance(eval_report, (bytes, bytearray)):
            raise BoundaryError("INVALID_RECORD", "eval_report는 bytes여야 한다")
        eval_report = bytes(eval_report)
        adapter_dir = Path(adapter_dir)
        if not adapter_dir.is_dir():
            raise BoundaryError("NOT_FOUND", "adapter_dir가 없다")
        if canonical.directory_hash(adapter_dir) != manifest["adapter_hash"]:
            raise BoundaryError("INTEGRITY_ERROR", "adapter_dir의 해시가 adapter_hash와 다르다")
        verify_system_prompt(adapter_dir, manifest["system_prompt"])
        with self._lock():
            existing = self._s_get_manifest(pid, ver)
            if existing is not None:
                if existing["content_hash"] == manifest["content_hash"]:
                    return copy.deepcopy(existing)
                raise BoundaryError("CONFLICT", "같은 (package_id, version)에 다른 내용이다")
            ref = manifest["eval_report_ref"]
            old = self._s_get_report(ref)
            if old is not None and old != eval_report:
                raise BoundaryError("CONFLICT", "같은 eval_report_ref에 다른 내용이다")
            self._s_commit(copy.deepcopy(manifest), adapter_dir, ref, eval_report)
        return copy.deepcopy(manifest)

    def get_manifest(self, package_id: str, version: str) -> dict:
        m = self._s_get_manifest(package_id, version)
        if m is None:
            raise BoundaryError("NOT_FOUND", "모델 패키지가 없다")
        if canonical.content_hash(m) != m["content_hash"]:
            raise BoundaryError("INTEGRITY_ERROR", "저장된 매니페스트가 변조되었다")
        return copy.deepcopy(m)

    def list_ids(self, *, cursor: Optional[str] = None, limit: int = 100) -> tuple:
        return self._page(self._s_ids(), cursor, limit)

    def list_versions(self, package_id: str) -> list[str]:
        return list(self._s_versions(package_id))

    def open_adapter(self, package_id: str, version: str, *, dest: Path) -> Path:
        m = self.get_manifest(package_id, version)
        out = Path(dest) / package_id / version
        if out.exists():
            shutil.rmtree(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        self._s_extract(package_id, version, out)
        if canonical.directory_hash(out) != m["adapter_hash"]:
            shutil.rmtree(out, ignore_errors=True)
            raise BoundaryError("INTEGRITY_ERROR", "어댑터의 해시가 adapter_hash와 다르다")
        return out

    def get_eval_report(self, ref: str) -> bytes:
        b = self._s_get_report(ref)
        if b is None:
            raise BoundaryError("NOT_FOUND", "평가 리포트가 없다")
        return b

    @staticmethod
    def _page(items: list, cursor: Optional[str], limit: int) -> tuple:
        if not isinstance(limit, int) or limit < 1:
            raise BoundaryError("INVALID_RECORD", "limit은 1 이상이어야 한다")
        try:
            start = int(cursor) if cursor is not None else 0
            if start < 0:
                raise ValueError
        except ValueError:
            raise BoundaryError("INVALID_RECORD", "cursor가 올바르지 않다") from None
        page = items[start:start + limit]
        nxt = str(start + limit) if start + limit < len(items) else None
        return list(page), nxt


class MemoryModelRegistry(_Core):
    """I3 메모리 구현. 이웃(release-gate)이 혼자 시험할 때 쓴다."""

    def __init__(self) -> None:
        super().__init__()
        self._m: dict[tuple, dict] = {}
        self._ids: list[str] = []
        self._vers: dict[str, list[str]] = {}
        self._files: dict[tuple, dict[str, bytes]] = {}
        self._reports: dict[str, bytes] = {}
        self._mu = threading.RLock()

    def _lock(self): return self._mu
    def _s_get_manifest(self, pid, ver): return self._m.get((pid, ver))
    def _s_get_report(self, ref): return self._reports.get(ref)

    def _s_commit(self, manifest, adapter_dir, ref, report):
        pid, ver = manifest["package_id"], manifest["version"]
        files = {p.relative_to(adapter_dir).as_posix(): p.read_bytes()
                 for p in sorted(adapter_dir.rglob("*")) if p.is_file()}
        self._files[(pid, ver)] = files
        self._reports[ref] = report
        if pid not in self._vers:
            self._ids.append(pid)
            self._vers[pid] = []
        self._vers[pid].append(ver)
        self._m[(pid, ver)] = manifest

    def _s_ids(self): return list(self._ids)
    def _s_versions(self, pid): return list(self._vers.get(pid, []))

    def _s_extract(self, pid, ver, dest):
        dest.mkdir(parents=True, exist_ok=True)
        for rel, data in self._files[(pid, ver)].items():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            (dest / rel).write_bytes(data)

    # 시험용: 저장된 어댑터 파일 하나를 바꾼다(변조 시뮬레이션)
    def _tamper_adapter(self, pid: str, ver: str) -> None:
        files = self._files[(pid, ver)]
        k = sorted(files)[0]
        files[k] = files[k] + b"x"


class FileModelRegistry(_Core):
    """I3 파일 구현. root 아래 manifests/, adapters/, eval_reports/ 와 ids.json(처음 게시된 순서)."""

    def __init__(self, root: str | Path) -> None:
        super().__init__()
        self.root = Path(root)
        (self.root / "manifests").mkdir(parents=True, exist_ok=True)
        (self.root / "adapters").mkdir(parents=True, exist_ok=True)
        (self.root / "eval_reports").mkdir(parents=True, exist_ok=True)

    class _FLock:
        def __init__(self, path: Path): self.path = path
        def __enter__(self):
            self.f = open(self.path, "a+")
            fcntl.flock(self.f, fcntl.LOCK_EX)
        def __exit__(self, *a):
            fcntl.flock(self.f, fcntl.LOCK_UN)
            self.f.close()

    def _lock(self): return self._FLock(self.root / ".lock")

    def _mpath(self, pid, ver): return self.root / "manifests" / pid / f"{ver}.json"
    def _order(self, pid): return self.root / "manifests" / pid / "_order.json"
    def _read_json(self, p: Path, default):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except ValueError:
            raise BoundaryError("INTEGRITY_ERROR", "레지스트리 파일이 깨졌다") from None

    @staticmethod
    def _atomic_write(p: Path, data: bytes) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)

    def _s_get_manifest(self, pid, ver):
        try:
            _check_id(pid, "package_id")
            _check_id(ver, "version")
        except BoundaryError:
            return None
        return self._read_json(self._mpath(pid, ver), None)

    def _s_get_report(self, ref):
        try:
            _check_id(ref, "eval_report_ref")
        except BoundaryError:
            return None
        p = self.root / "eval_reports" / ref
        return p.read_bytes() if p.is_file() else None

    def _s_commit(self, manifest, adapter_dir, ref, report):
        pid, ver = manifest["package_id"], manifest["version"]
        final = self.root / "adapters" / pid / ver
        tmp = self.root / "adapters" / pid / f".tmp-{ver}"
        if tmp.exists():
            shutil.rmtree(tmp)
        if final.exists():          # 매니페스트 없는 잔해(이전 게시 중단)
            shutil.rmtree(final)
        _copy_tree(adapter_dir, tmp)
        os.replace(tmp, final)
        self._atomic_write(self.root / "eval_reports" / ref, report)
        ids = self._read_json(self.root / "ids.json", [])
        if pid not in ids:
            ids.append(pid)
            self._atomic_write(self.root / "ids.json", json.dumps(ids).encode())
        order = self._read_json(self._order(pid), [])
        order.append(ver)
        self._atomic_write(self._order(pid), json.dumps(order).encode())
        # 매니페스트가 마지막 쓰기다. 이것이 있어야 게시된 것이다.
        self._atomic_write(self._mpath(pid, ver), canonical.canonicalize(manifest))

    def _s_ids(self): return self._read_json(self.root / "ids.json", [])

    def _s_versions(self, pid):
        order = self._read_json(self._order(pid), [])
        return [v for v in order if self._mpath(pid, v).is_file()]

    def _s_extract(self, pid, ver, dest):
        _copy_tree(self.root / "adapters" / pid / ver, dest)

    def _tamper_adapter(self, pid: str, ver: str) -> None:
        files = sorted(p for p in (self.root / "adapters" / pid / ver).rglob("*") if p.is_file())
        files[0].write_bytes(files[0].read_bytes() + b"x")
