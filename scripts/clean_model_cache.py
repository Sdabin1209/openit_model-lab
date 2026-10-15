"""HF 모델 캐시 정리. 캐시는 blob을 공유하는 구조라 디렉터리를 직접 지우면 안 된다. huggingface_hub의 삭제 전략을 쓴다.
사용: python scripts/clean_model_cache.py                    # 캐시 목록과 크기(읽기만)
      python scripts/clean_model_cache.py Qwen/Qwen3-8B      # dry-run: 지울 때 확보되는 크기만 표시
      python scripts/clean_model_cache.py Qwen/Qwen3-8B --yes   # 실제 삭제(사용자 워크스테이션이므로 사용자 승인 뒤에만)
미완성(.incomplete) 조각만 남은 모델은 리비전이 없어 delete_revisions로 지워지지 않는다: 이 경우 안내만 하고 `--yes`일 때 해당 모델 폴더의 .incomplete 파일만 지운다."""
from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str], hub: Path | None = None) -> int:
    from huggingface_hub import scan_cache_dir
    import os
    hub = hub or Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    info = scan_cache_dir(hub)
    print(f"캐시 {hub}: {info.size_on_disk / 2**30:.2f}GB")
    for r in sorted(info.repos, key=lambda r: r.repo_id):
        print(f"  {r.size_on_disk / 2**30:6.2f}GB  {r.repo_id}  리비전 {[v.commit_hash[:8] for v in r.revisions]}")
    if not argv:
        return 0
    target, yes = argv[0], "--yes" in argv[1:]
    repo = next((r for r in info.repos if r.repo_id == target), None)
    if repo is None:
        print(f"캐시에 없음: {target}")
        return 1
    strategy = info.delete_revisions(*[v.commit_hash for v in repo.revisions])
    print(f"대상 {target}: 지우면 {strategy.expected_freed_size / 2**30:.2f}GB 확보")
    if not yes:
        print("(dry-run: 실제로 지우려면 --yes)")
        return 0
    strategy.execute()
    print("지움")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
