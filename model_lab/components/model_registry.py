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


def _copy_tree(src: Path, dst: Path, *, link: bool = False) -> None:
    """link=True면 같은 파일 시스템에서 하드 링크를 먼저 시도해 디스크를 더 쓰지 않는다(ML-2). 원본이 이후 바뀌지 않는다고
    보장될 때(서비스가 풀어 둔 임시 폴더)만 켠다. 링크할 수 없으면 복사한다."""
    dst.mkdir(parents=True, exist_ok=True)
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if p.is_dir():
            (dst / rel).mkdir(parents=True, exist_ok=True)
        elif p.is_file():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            if link:
                try:
                    os.link(p, dst / rel)
                    continue
                except OSError:
                    pass
            shutil.copyfile(p, dst / rel)


def verify_system_prompt(adapter_dir: Path, ref: dict) -> None:
    """어댑터 디렉터리 안에 시스템 프롬프트 파일이 있고 해시가 sha256과 같아야 한다(D75, 어기면 INTEGRITY_ERROR)."""
    root = Path(adapter_dir).resolve()
    f = (root / ref["path"]).resolve()
    if root not in f.parents or not f.is_file() or canonical.file_hash(f) != ref["sha256"]:
        raise BoundaryError("INTEGRITY_ERROR", "시스템 프롬프트 파일이 없거나 system_prompt.sha256과 다르다")


def layout_of(adapter_dir: Path, role: str) -> dict:
    """어댑터 디렉터리의 ModelPackageLayout 문서(상대 경로 -> 파일 해시, adapter_hash, 시스템 프롬프트). 계약 스키마로 검증해 돌려준다.
    왜 스키마로: 어댑터 디렉터리에 들어갈 수 있는 파일을 손으로 나열해 검사하던 것을 계약(필수 3개, 모르는 파일 거부)에 맡긴다(D107).
    어기면 INVALID_RECORD. 시스템 프롬프트 파일이 매니페스트의 sha256과 같은지는 verify_system_prompt가 본다."""
    root = Path(adapter_dir)
    files = {p.relative_to(root).as_posix(): canonical.file_hash(p) for p in sorted(root.rglob("*")) if p.is_file()}
    doc = {"role": role, "adapter_files": files, "adapter_hash": canonical.directory_hash(root),
           "system_prompt": {"path": "system_prompt.txt", "sha256": files.get("system_prompt.txt")}}
    errs = sorted(validator("ModelPackageLayout").iter_errors(doc), key=lambda e: list(e.absolute_path))
    if errs:
        path = "/" + "/".join(map(str, errs[0].absolute_path))
        raise BoundaryError("INVALID_RECORD", f"ModelPackageLayout 위반: {path}")
    return doc


class _Core:
    """저장 방식과 무관한 규칙. 하위 클래스는 _Storage 동작(아래 _s_*)만 구현한다."""

    def __init__(self) -> None:
        self._validate = validator("ModelPackageManifest").validate

    # --- 저장소 동작(하위 클래스) ---
    def _s_get_manifest(self, pid: str, ver: str) -> Optional[dict]: raise NotImplementedError
    def _s_get_report(self, ref: str) -> Optional[bytes]: raise NotImplementedError
    def _s_commit(self, manifest: dict, adapter_dir: Path, ref: str, report: bytes, publisher: Optional[dict] = None) -> None: raise NotImplementedError
    def _s_ids(self) -> list[str]: raise NotImplementedError
    def _s_versions(self, pid: str) -> list[str]: raise NotImplementedError
    def _s_extract(self, pid: str, ver: str, dest: Path) -> None: raise NotImplementedError
    def _lock(self): raise NotImplementedError

    # --- I3 ---
    def publish(self, manifest: dict, adapter_dir: Path, eval_report: bytes, *, publisher: Optional[dict] = None) -> dict:
        """publisher: 게시자 기록(ML-6; 예: {"role": ..., "remote": ...}). 매니페스트는 content_hash로 봉인되어 서버가 고칠 수 없으므로
        파일 구현은 매니페스트 옆 별도 기록에 남긴다. 이미 게시된 것을 다시 게시(멱등)해도 최초 기록을 덮어쓰지 않는다."""
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
        layout_of(adapter_dir, manifest["role"])
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
            self._s_commit(copy.deepcopy(manifest), adapter_dir, ref, eval_report, publisher)
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

    def _s_commit(self, manifest, adapter_dir, ref, report, publisher=None):
        pid, ver = manifest["package_id"], manifest["version"]
        files = {p.relative_to(adapter_dir).as_posix(): p.read_bytes()
                 for p in sorted(adapter_dir.rglob("*")) if p.is_file()}
        self._files[(pid, ver)] = files
        self._reports[ref] = report
        if pid not in self._vers:
            self._ids.append(pid)
            self._vers[pid] = []
        if ver not in self._vers[pid]:
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

    def __init__(self, root: str | Path, *, link_on_commit: bool = False) -> None:
        super().__init__()
        self.link_on_commit = link_on_commit      # 서비스처럼 원본이 임시 폴더일 때만 True(하드 링크로 디스크 절약)
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

    def _s_commit(self, manifest, adapter_dir, ref, report, publisher=None):
        pid, ver = manifest["package_id"], manifest["version"]
        final = self.root / "adapters" / pid / ver
        tmp = self.root / "adapters" / pid / f".tmp-{ver}"
        if tmp.exists():
            shutil.rmtree(tmp)
        if final.exists():          # 매니페스트 없는 잔해(이전 게시 중단)
            shutil.rmtree(final)
        _copy_tree(adapter_dir, tmp, link=self.link_on_commit)
        os.replace(tmp, final)
        self._atomic_write(self.root / "eval_reports" / ref, report)
        ids = self._read_json(self.root / "ids.json", [])
        if pid not in ids:
            ids.append(pid)
            self._atomic_write(self.root / "ids.json", json.dumps(ids).encode())
        order = self._read_json(self._order(pid), [])
        if ver not in order:        # 중단 뒤 재시도(ML-3): 이미 order에 있으면 다시 넣지 않는다
            order.append(ver)
            self._atomic_write(self._order(pid), json.dumps(order).encode())
        if publisher:
            self._atomic_write(self.root / "manifests" / pid / f"{ver}.published.json",
                               json.dumps({**publisher, "content_hash": manifest["content_hash"]}, ensure_ascii=False, sort_keys=True).encode())
        # 매니페스트가 마지막 쓰기다. 이것이 있어야 게시된 것이다.
        self._atomic_write(self._mpath(pid, ver), canonical.canonicalize(manifest))

    def _s_ids(self): return self._read_json(self.root / "ids.json", [])

    def _s_versions(self, pid):
        order = self._read_json(self._order(pid), [])
        seen: set = set()
        return [v for v in order if v not in seen and not seen.add(v) and self._mpath(pid, v).is_file()]   # 중복 제거(ML-3)

    def _s_extract(self, pid, ver, dest):
        _copy_tree(self.root / "adapters" / pid / ver, dest)

    def get_publisher(self, package_id: str, version: str) -> Optional[dict]:
        """게시자 기록(ML-6). 없으면 None(기록 도입 전에 게시된 패키지)."""
        try:
            _check_id(package_id, "package_id"); _check_id(version, "version")
        except BoundaryError:
            return None
        return self._read_json(self.root / "manifests" / package_id / f"{version}.published.json", None)

    def recover(self) -> int:
        """시작 때 호출한다(ML-3). 게시 중단 잔해(adapters/*/.tmp-*, manifests/**/.tmp-*)를 지우고 지운 개수를 돌려준다.
        매니페스트 없이 남은 final 디렉터리는 다음 게시가 지우고 다시 쓰므로 여기서 건드리지 않는다."""
        n = 0
        with self._lock():      # 같은 루트를 쓰는 다른 프로세스의 게시가 만드는 .tmp-*를 지우지 않도록 게시와 같은 잠금 안에서 한다
            for base in ("adapters", "manifests"):
                for p in (self.root / base).rglob(".tmp-*"):
                    shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
                    n += 1
        return n

    def _tamper_adapter(self, pid: str, ver: str) -> None:
        files = sorted(p for p in (self.root / "adapters" / pid / ver).rglob("*") if p.is_file())
        files[0].write_bytes(files[0].read_bytes() + b"x")
