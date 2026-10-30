"""I2 데이터셋 읽기 통신 컴포넌트(보내는 쪽). 이웃 synthetic-data의 읽기 SDK/HTTP 클라이언트로 버전을 정하고 파일을 내려받아 해시를 확인한다.

입력 DatasetFetchRequest {dataset_id, version, dest, runtime_contract_version}(store는 wiring이 주입), 출력 DatasetSnapshot {manifest(계약 DatasetManifest)}.
계약 타입(DatasetManifest) <-> 영역 내부 타입 변환은 여기서 끝난다: 업무 컴포넌트는 HTTP도 SDK도 모른다."""
from __future__ import annotations

from pathlib import Path

from boundary_contracts import BoundaryError, canonical, validator
from openit_link.clients import HttpDatasetStore


def connect(**kw):
    """원격 dataset-store 클라이언트(D100). 인자는 wiring/factories.resolve_args가 비밀을 풀어 넘긴다."""
    return HttpDatasetStore(**kw)


def resolve_version(store, dataset_id: str, version: str | None) -> str:
    """version이 없으면 가장 최근(list_versions는 오래된 것부터이므로 마지막)."""
    if version:
        return version
    versions = store.list_versions(dataset_id)
    if not versions:
        raise BoundaryError("NOT_FOUND", "게시된 데이터셋 버전이 없다")
    return versions[-1]


def fetch_dataset(store, dataset_id: str, version: str, dest: Path, *, contract_version: int) -> dict:
    """매니페스트를 읽고 모든 파일을 dest에 내려받아 해시를 확인한다. 읽은 매니페스트를 낸다."""
    manifest = store.get_manifest(dataset_id, version)
    try:
        validator("DatasetManifest").validate(manifest)
    except Exception:
        raise BoundaryError("INVALID_RECORD", "DatasetManifest 위반") from None
    if canonical.content_hash(manifest) != manifest["content_hash"]:
        raise BoundaryError("INTEGRITY_ERROR", "DatasetManifest의 content_hash가 맞지 않는다")
    if manifest["runtime_contract_version"] != contract_version:
        raise BoundaryError("UNSUPPORTED_VERSION", "데이터셋의 문진 스키마 정수 버전이 다르다")
    dest.mkdir(parents=True, exist_ok=True)
    for f in manifest["files"]:
        out = dest / f["path"]
        out.parent.mkdir(parents=True, exist_ok=True)
        with store.open_file(dataset_id, version, f["path"]) as src, open(out, "wb") as dst:
            for chunk in iter(lambda: src.read(1 << 20), b""):
                dst.write(chunk)
        if canonical.file_hash(out) != f["content_hash"]:
            raise BoundaryError("INTEGRITY_ERROR", "데이터셋 파일의 해시가 맞지 않는다")
    return manifest
