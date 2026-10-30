"""실행 기록 컴포넌트: run.json(RunRecord, schemas/)을 원자적으로 쓰고 읽고, 단계 상태를 표시한다.

입력은 RunRecord 문서와 경로, 출력은 갱신된 RunRecord. 같은 입력 -> 같은 파일 내용(시각은 호출하는 쪽이 정해 넘긴다)."""
from __future__ import annotations

import json
import os
from pathlib import Path


def atomic_json(path: Path, obj) -> None:
    # 왜 임시 파일 + os.replace: 쓰는 도중 멈춰도 run.json이 반쯤 쓰인 채 남지 않는다(POSIX rename은 원자적).
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def load(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def is_done(rec: dict, *path: str) -> bool:
    d = rec["stages"]
    for p in path:
        d = d.get(p, {})
    return d.get("status") == "done"


def mark(rec: dict, stage: str, role: str | None, **info) -> None:
    d = rec["stages"].setdefault(stage, {})
    if role:
        d = d.setdefault(role, {})
    d.update(info)
