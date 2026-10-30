"""D117 6절 구조 검사(head/docs/DESIGN.md). 헤드의 head/scripts/structure-check.py와 같은 기준을 영역 안에서도 강제한다."""
import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import ROOT

PKG = ROOT / "model_lab"
HTTP = re.compile(r"^\s*(from|import)\s+(boundary_contracts\.clients|openit_link\.clients|urllib\.request|http\.client|http\.server|requests|waitress|wsgiref)\b", re.M)
ENTRY = re.compile(r"(^|/)(__main__|main|app|server|config|settings|migrate|cli|index)\.py$")


def _sibling():
    p = Path(sys.executable).parent / "datamodel-codegen"
    return str(p) if p.exists() else None


def code_files():
    return [p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts]


def test_1_required_directories_exist():
    for d in ("schemas", "model_lab/types", "model_lab/components", "model_lab/wiring"):
        assert (ROOT / d).is_dir() and any((ROOT / d).iterdir()), d


def test_2_components_do_not_import_each_other():
    comps = {p.stem for p in (PKG / "components").glob("*.py") if p.stem != "__init__"}
    for p in (PKG / "components").glob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            mods = []
            if isinstance(n, ast.ImportFrom):
                mods = [(n.module or "", n.level, [a.name for a in n.names])]
            elif isinstance(n, ast.Import):
                mods = [(a.name, 0, []) for a in n.names]
            for mod, level, names in mods:
                parts = mod.split(".") if mod else []
                hit = set()
                if level == 1:                         # from .x import y / from . import x
                    hit = set(parts[:1]) | (set(names) if not parts else set())
                elif "components" in parts:            # from ..components.x / from model_lab.components import x
                    i = parts.index("components")
                    hit = set(parts[i + 1:i + 2]) | (set(names) if i == len(parts) - 1 else set())
                assert not (hit & (comps - {p.stem})), f"{p.name}가 다른 컴포넌트 {hit & comps}를 import한다"


def test_3_http_only_in_link_components_or_entrypoints():
    for p in code_files():
        rel = p.relative_to(ROOT).as_posix()
        if HTTP.search(p.read_text(encoding="utf-8")):
            assert p.name.startswith("link_") or ENTRY.search(rel), f"{rel}: 통신 컴포넌트(link_*) 밖에서 HTTP"


def test_6_every_code_file_belongs_somewhere():
    for p in code_files():
        rel = p.relative_to(PKG).as_posix()
        ok = rel.startswith(("components/", "wiring/", "types/")) or ENTRY.search(rel) or p.name == "__init__.py"
        assert ok, f"{rel}: components/·wiring/·types/·진입점 어디에도 속하지 않음"


def test_5_types_match_regenerated_output(tmp_path):
    exe = os.environ.get("DATAMODEL_CODEGEN") or shutil.which("datamodel-codegen") or _sibling()
    if not exe:
        pytest.skip("datamodel-codegen이 없다(requirements-codegen.txt). 이 환경에서는 types/ 재생성 비교를 하지 못했다")
    out = tmp_path / "types"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_types.py"), "--out", str(out)], check=True,
                   env={**os.environ, "DATAMODEL_CODEGEN": exe}, capture_output=True)
    for p in sorted(out.rglob("*.py")):
        assert (PKG / "types" / p.relative_to(out)).read_text(encoding="utf-8") == p.read_text(encoding="utf-8"), p.name
    assert {p.name for p in out.rglob("*.py")} == {p.name for p in (PKG / "types").rglob("*.py")}


def test_types_are_importable():
    import importlib
    for p in (PKG / "types").glob("*_schema.py"):
        importlib.import_module(f"model_lab.types.{p.stem}")
