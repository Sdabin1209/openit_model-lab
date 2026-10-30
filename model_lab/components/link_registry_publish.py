"""I3 모델 게시 통신 컴포넌트(보내는 쪽). 원격 model-registry(HTTP.md I3)에 패키지를 게시한다.

입력 PackagePublication {manifest(계약 ModelPackageManifest), adapter_dir, eval_report(bytes)}(registry 클라이언트는 wiring이 주입), 출력 게시된 ModelPackageManifest.
멱등: 같은 내용이면 기존 것을 돌려준다(서버가 정한다)."""
from __future__ import annotations

from pathlib import Path

from openit_link.clients import HttpModelRegistry


def connect(**kw):
    """원격 model-registry 클라이언트. 인자(base_url, token, timeout)는 wiring/factories.resolve_args가 비밀을 풀어 넘긴다."""
    return HttpModelRegistry(**kw)


def publish(registry, manifest: dict, adapter_dir: Path, eval_report: bytes) -> dict:
    return registry.publish(manifest, adapter_dir, eval_report)
