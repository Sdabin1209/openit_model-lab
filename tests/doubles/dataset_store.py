"""I2 DatasetStore의 시험용 대역(이웃 synthetic-data의 실제 SDK가 아니다).

boundary_contracts.sdk.DatasetStore Protocol을 따른다. 메모리 구현과, 디렉터리에서 읽는 구현이 있다.
CONVENTIONS의 규칙(멱등, CONFLICT, 해시 검증, 평가-학습 시나리오 분리)을 최소한으로 흉내 낸다."""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import BinaryIO, Mapping

from boundary_contracts import BoundaryError, canonical, validator


class MemoryDatasetStore:
    def __init__(self) -> None:
        self._m: dict[tuple, dict] = {}
        self._f: dict[tuple, bytes] = {}
        self._v: dict[str, list[str]] = {}

    def publish(self, manifest: dict, files: Mapping[str, Path]) -> dict:
        try:
            validator("DatasetManifest").validate(manifest)
        except Exception:
            raise BoundaryError("INVALID_RECORD", "DatasetManifest 위반") from None
        if canonical.content_hash(manifest) != manifest["content_hash"]:
            raise BoundaryError("INTEGRITY_ERROR", "content_hash 불일치")
        key = (manifest["dataset_id"], manifest["version"])
        if key in self._m:
            if self._m[key]["content_hash"] == manifest["content_hash"]:
                return self._m[key]
            raise BoundaryError("CONFLICT", "같은 버전에 다른 내용")
        data = {}
        scn: dict[str, set] = {"EVAL": set(), "TV": set()}
        for f in manifest["files"]:
            p = Path(files[f["path"]])
            if canonical.file_hash(p) != f["content_hash"]:
                raise BoundaryError("INTEGRITY_ERROR", "파일 해시 불일치")
            rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
            if len(rows) != f["sample_count"]:
                raise BoundaryError("INVALID_RECORD", "sample_count 불일치")
            for r in rows:
                scn["EVAL" if f["split"] == "EVAL" else "TV"].add(r["scenario_id"])
            data[f["path"]] = p.read_bytes()
        if scn["EVAL"] & scn["TV"]:
            raise BoundaryError("INVALID_RECORD", "평가 시나리오가 학습·검증과 겹친다")
        self._m[key] = manifest
        for path, b in data.items():
            self._f[(*key, path)] = b
        self._v.setdefault(key[0], []).append(key[1])
        return manifest

    def get_manifest(self, dataset_id: str, version: str) -> dict:
        try:
            return self._m[(dataset_id, version)]
        except KeyError:
            raise BoundaryError("NOT_FOUND", "데이터셋이 없다") from None

    def list_versions(self, dataset_id: str) -> list[str]:
        return list(self._v.get(dataset_id, []))

    def open_file(self, dataset_id: str, version: str, path: str) -> BinaryIO:
        try:
            return io.BytesIO(self._f[(dataset_id, version, path)])
        except KeyError:
            raise BoundaryError("NOT_FOUND", "파일이 없다") from None


class DirDatasetStore:
    """root/<dataset_id>/<version>/{manifest.json, <files>} 를 읽는다. 읽기 전용(publish는 시험용 헬퍼가 따로 쓴다)."""

    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def publish(self, manifest, files):
        raise BoundaryError("PERMISSION_DENIED", "읽기 전용 대역")

    def get_manifest(self, dataset_id: str, version: str) -> dict:
        p = self.root / dataset_id / version / "manifest.json"
        if not p.is_file():
            raise BoundaryError("NOT_FOUND", "데이터셋이 없다")
        return json.loads(p.read_text(encoding="utf-8"))

    def list_versions(self, dataset_id: str) -> list[str]:
        order = self.root / dataset_id / "versions.json"      # 오래된 것부터
        return json.loads(order.read_text()) if order.is_file() else []

    def open_file(self, dataset_id: str, version: str, path: str) -> BinaryIO:
        p = self.root / dataset_id / version / path
        if not p.is_file():
            raise BoundaryError("NOT_FOUND", "파일이 없다")
        return open(p, "rb")


def from_dir(root: str) -> DirDatasetStore:
    return DirDatasetStore(root)
