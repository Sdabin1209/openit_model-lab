"""설정의 읽기·게시 대상 명세로 이웃 SDK/클라이언트와 레지스트리를 만들어 컴포넌트에 주입한다(wiring)."""
from __future__ import annotations

from ..components.model_registry import FileModelRegistry
from . import factories
from .settings import resolved_path


def make_dataset_store(cfg: dict):
    """읽기 대상(I2). kind=http = 원격 dataset-store(D100, 기본), factory = 임의 구현(시험 대역·로컬 파일 읽기). 필수 필드는 스키마 RunConfig가 본다."""
    spec = cfg["inputs"]["dataset_store"]
    if spec.get("kind") == "http":
        return factories.build_http("dataset_store", spec)
    return factories.build(spec)


def make_registry(cfg: dict):
    """게시 대상(I3). file = 로컬 파일 구현, http = 원격 model-registry, factory = 임의 구현."""
    spec = cfg["registry"]
    if spec["kind"] == "file":
        return FileModelRegistry(resolved_path(cfg, spec.get("path", "registry")))
    if spec["kind"] == "http":
        return factories.build_http("model_registry", spec)
    return factories.build(spec)
