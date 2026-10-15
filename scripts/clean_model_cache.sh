#!/bin/sh
# 모델 캐시(~/.cache/huggingface/hub)의 크기를 보여 주고, 지정한 모델만 지운다. 기본은 보기만 한다(dry-run).
# 사용: scripts/clean_model_cache.sh                      # 크기와 미완성 조각 표시
#       scripts/clean_model_cache.sh Qwen/Qwen3-8B        # 지울 대상과 크기만 표시
#       scripts/clean_model_cache.sh Qwen/Qwen3-8B --yes  # 실제로 지운다(사용자 워크스테이션이므로 사용자 승인 뒤에만)
# 지우는 것은 hub 안의 models--<이름> 폴더 하나뿐이다. 다시 필요하면 내려받는다.
set -eu
HUB="${HF_HOME:-$HOME/.cache/huggingface}/hub"
df -h "$HUB" 2>/dev/null | tail -1
echo "캐시 전체: $(du -sh "$HUB" 2>/dev/null | cut -f1)"
for d in "$HUB"/models--*; do [ -d "$d" ] && echo "  $(du -shL "$d" | cut -f1)  $(basename "$d")  미완성 조각 $(find "$d" -name '*.incomplete' | wc -l)개"; done
[ $# -ge 1 ] || exit 0
TARGET="$HUB/models--$(echo "$1" | sed 's|/|--|g')"
case "$TARGET" in "$HUB"/models--?*) ;; *) echo "잘못된 대상"; exit 2;; esac
[ -d "$TARGET" ] || { echo "없음: $TARGET"; exit 1; }
echo "대상: $TARGET ($(du -shL "$TARGET" | cut -f1))"
if [ "${2:-}" = "--yes" ]; then rm -rf -- "$TARGET"; echo "지움"; else echo "(dry-run: 지우려면 --yes)"; fi
