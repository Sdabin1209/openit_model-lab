"""I2 데이터셋 파일 저장소의 독립 읽기 구현(synthetic-data의 코드를 가져오지 않는다).

레이아웃(synthetic-data boundary.md 1절): `<root>/<dataset_id>/<version>/{manifest.json, meta.json, files/<path>}`.
`boundary_contracts.sdk.DatasetStore` Protocol을 따르며 읽기만 한다(`publish`는 PERMISSION_DENIED).
- get_manifest: content_hash를 다시 계산해 확인한다(INTEGRITY_ERROR).
- open_file: manifest의 파일 해시를 다시 확인한 뒤 연다(INTEGRITY_ERROR).
- list_versions: 게시 순서(오래된 것부터). `meta.json`의 정수 `seq`가 있으면 그것, 없으면 manifest.created_at, 그다음 버전 이름 순.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import BinaryIO, Mapping

from boundary_contracts import BoundaryError, canonical

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def _check(value: str, what: str) -> None:
    if not isinstance(value, str) or len(value) > 128 or not _ID.match(value):
        raise BoundaryError("INVALID_RECORD", f"{what}가 Id 규칙을 어겼다")


class FileDatasetReader:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def publish(self, manifest: dict, files: Mapping[str, Path]) -> dict:
        raise BoundaryError("PERMISSION_DENIED", "읽기 전용 구현이다(데이터셋을 쓰는 영역은 synthetic-data뿐)")

    def _dir(self, dataset_id: str, version: str) -> Path:
        _check(dataset_id, "dataset_id"), _check(version, "version")
        return self.root / dataset_id / version

    @staticmethod
    def _json(p: Path):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise BoundaryError("NOT_FOUND", "데이터셋이 없다") from None
        except (ValueError, OSError):
            raise BoundaryError("INTEGRITY_ERROR", "저장된 파일을 읽을 수 없다") from None

    def get_manifest(self, dataset_id: str, version: str) -> dict:
        m = self._json(self._dir(dataset_id, version) / "manifest.json")
        if not isinstance(m, dict) or canonical.content_hash(m) != m.get("content_hash"):
            raise BoundaryError("INTEGRITY_ERROR", "매니페스트의 content_hash가 맞지 않는다")
        if (m.get("dataset_id"), m.get("version")) != (dataset_id, version):
            raise BoundaryError("INTEGRITY_ERROR", "매니페스트가 요청한 버전과 다르다")
        return m

    def list_versions(self, dataset_id: str) -> list[str]:
        _check(dataset_id, "dataset_id")
        base = self.root / dataset_id
        if not base.is_dir():
            return []
        keyed = []
        for d in base.iterdir():
            if d.name.startswith(".") or not (d / "manifest.json").is_file():   # 임시 디렉터리·반쯤 쓴 버전은 건너뛴다
                continue
            seq, created = None, ""
            try:
                seq = json.loads((d / "meta.json").read_text(encoding="utf-8")).get("seq")
            except (OSError, ValueError, AttributeError):
                pass
            if not isinstance(seq, int):
                try:
                    created = json.loads((d / "manifest.json").read_text(encoding="utf-8")).get("created_at", "")
                except (OSError, ValueError):
                    pass
            keyed.append(((0, seq, "", d.name) if isinstance(seq, int) else (1, 0, created, d.name), d.name))
        return [n for _, n in sorted(keyed)]

    def open_file(self, dataset_id: str, version: str, path: str) -> BinaryIO:
        m = self.get_manifest(dataset_id, version)
        entry = next((f for f in m["files"] if f["path"] == path), None)
        if entry is None:
            raise BoundaryError("NOT_FOUND", "매니페스트에 없는 파일이다")
        p = self._dir(dataset_id, version) / "files" / path
        if ".." in Path(path).parts or Path(path).is_absolute():
            raise BoundaryError("INVALID_RECORD", "파일 경로가 안전하지 않다")
        if not p.is_file():
            raise BoundaryError("NOT_FOUND", "파일이 없다")
        if canonical.file_hash(p) != entry["content_hash"]:
            raise BoundaryError("INTEGRITY_ERROR", "파일 해시가 맞지 않는다")
        return open(p, "rb")


def file_dataset_store(root: str) -> FileDatasetReader:
    """설정용 팩토리: `{factory: "model_lab.file_dataset_store:file_dataset_store", args: {root_env: DATASET_ROOT}}`"""
    return FileDatasetReader(root)
