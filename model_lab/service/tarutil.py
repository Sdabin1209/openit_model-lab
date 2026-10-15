"""tar 스트림 묶기·풀기(HTTP.md): 경로에 '..'·절대 경로·심볼릭 링크를 두지 않는다."""
from __future__ import annotations

import tarfile
from pathlib import Path

from boundary_contracts import BoundaryError


def pack(files: dict[str, Path], out_path: Path) -> None:
    """files: tar 안의 경로 -> 디스크 파일. 디렉터리는 파일 경로로 만들어진다."""
    with tarfile.open(out_path, "w") as tf:
        for name in sorted(files):
            info = tf.gettarinfo(str(files[name]), arcname=name)
            info.uid = info.gid = 0; info.uname = info.gname = ""; info.mtime = 0
            with open(files[name], "rb") as f:
                tf.addfile(info, f)


def tree_files(root: Path, prefix: str = "") -> dict[str, Path]:
    return {(prefix + p.relative_to(root).as_posix()): p for p in sorted(root.rglob("*")) if p.is_file()}


def _safe(name: str) -> bool:
    p = Path(name)
    return bool(name) and not p.is_absolute() and ".." not in p.parts and not name.startswith("/")


def unpack(tar_path: Path, dest: Path, *, max_bytes: int) -> None:
    """안전하게 푼다. 일반 파일만 허용한다(디렉터리 항목은 무시)."""
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with tarfile.open(tar_path, "r") as tf:
            for m in tf:
                if m.isdir():
                    continue
                if not m.isreg() or not _safe(m.name):
                    raise BoundaryError("INVALID_RECORD", "tar에 허용되지 않는 항목이 있다")
                total += m.size
                if total > max_bytes:
                    raise BoundaryError("INVALID_RECORD", "tar가 너무 크다")
                out = dest / m.name
                out.parent.mkdir(parents=True, exist_ok=True)
                with tf.extractfile(m) as src, open(out, "wb") as dst:
                    while chunk := src.read(1 << 20):
                        dst.write(chunk)
    except tarfile.TarError:
        raise BoundaryError("INVALID_RECORD", "tar를 읽을 수 없다") from None
