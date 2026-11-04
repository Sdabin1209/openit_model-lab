"""복구한 models-data의 검증(진입점 연결): 모든 패키지의 매니페스트(content_hash), 평가 리포트, 어댑터 해시(open_adapter가 검증)를 읽는다.
link_backup 컴포넌트는 레지스트리 구현을 모르고, 이 연결이 파일 구현(model_registry)을 주입한다(구조 규칙 3)."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ..components.model_registry import FileModelRegistry


def verify_data(data: Path) -> int:
    """패키지 수를 돌려준다. 하나라도 읽히지 않거나 해시가 다르면 BoundaryError를 던진다."""
    reg = FileModelRegistry(data)
    n = 0
    ids, _ = reg.list_ids(limit=10**6)
    with tempfile.TemporaryDirectory() as td:
        for pid in ids:
            for ver in reg.list_versions(pid):
                m = reg.get_manifest(pid, ver)
                reg.get_eval_report(m["eval_report_ref"])
                reg.open_adapter(pid, ver, dest=Path(td))
                shutil.rmtree(Path(td) / pid, ignore_errors=True)
                n += 1
    return n
