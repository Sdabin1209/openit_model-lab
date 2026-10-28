"""기반 모델의 로컬 캐시 조회. huggingface_hub의 공개 API(`snapshot_download(local_files_only=True)`)만 쓴다.

왜(점검 T24): 이전에는 캐시 디렉터리 배치(`models--*/refs/*`)를 직접 해석하고 transformers의 비공개 속성 `config._commit_hash`를 읽었다.
둘 다 라이브러리 내부 사정이 바뀌면 조용히 틀린다. 반환 경로의 마지막 요소가 커밋 해시라는 것은 HF 캐시 문서의 `snapshots/<commit_hash>/` 배치다.
"""
from __future__ import annotations

from pathlib import Path


def cached_snapshot(model_id: str, revision: str | None = None) -> Path | None:
    """받아 둔 스냅샷 경로. 없으면 None. huggingface_hub가 없으면 ImportError를 그대로 낸다(호출자가 '확인 못 함'으로 다룬다)."""
    from huggingface_hub import snapshot_download
    try:
        p = Path(snapshot_download(model_id, revision=revision or "main", local_files_only=True))
    except Exception:   # noqa: BLE001  캐시에 없음(LocalEntryNotFoundError), 오프라인 설정, 잘못된 id 모두 '없음'이다
        return None
    return p if p.is_dir() and any(f.is_file() for f in p.rglob("*")) else None


def cached_commit(model_id: str, revision: str | None = None) -> str | None:
    p = cached_snapshot(model_id, revision)
    return p.name if p else None
