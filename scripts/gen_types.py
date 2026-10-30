#!/usr/bin/env python3
"""schemas/ -> model_lab/types/ (TypedDict). 손으로 고치지 않는다(D117 3절). 사용: python3 scripts/gen_types.py [--out DIR]

도구: datamodel-code-generator(requirements-codegen.txt가 버전을 고정한다). 환경 변수 DATAMODEL_CODEGEN이 실행 파일 경로(기본: PATH의 datamodel-codegen).
계약 스키마를 가리키는 `$ref`(예: ServingConfig.schema.json)는 이 영역이 정의한 타입이 아니므로 생성 입력에서 `object`로 바꾼다(타입 정의는 boundary-contracts가 한다, DESIGN 3절)."""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _sibling():
    """현재 파이썬과 같은 가상환경의 bin에 있는 도구(PATH에 venv가 없을 때)."""
    p = pathlib.Path(sys.executable).parent / "datamodel-codegen"
    return str(p) if p.exists() else None


def prepare(dst: pathlib.Path) -> None:
    own = {p.name for p in (ROOT / "schemas").glob("*.schema.json")}

    def walk(v):
        if isinstance(v, dict):
            ref = v.get("$ref")
            if isinstance(ref, str) and ref.endswith(".schema.json") and "#" not in ref and ref not in own:
                return {"type": "object", "description": "계약 " + ref.removesuffix(".schema.json")}
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    for p in sorted((ROOT / "schemas").glob("*.schema.json")):
        (dst / p.name).write_text(json.dumps(walk(json.loads(p.read_text(encoding="utf-8"))), ensure_ascii=False, indent=1), encoding="utf-8")


def generate(out: pathlib.Path) -> None:
    exe = os.environ.get("DATAMODEL_CODEGEN") or shutil.which("datamodel-codegen") or _sibling()
    if not exe:
        raise SystemExit("datamodel-codegen이 없다: pip install -r requirements-codegen.txt")
    with tempfile.TemporaryDirectory() as t:
        src = pathlib.Path(t) / "in"
        src.mkdir()
        prepare(src)
        if out.exists():
            shutil.rmtree(out)
        subprocess.run([exe, "--input", str(src), "--input-file-type", "jsonschema", "--output", str(out), "--output-model-type", "typing.TypedDict",
                        "--target-python-version", "3.10", "--disable-timestamp", "--formatters", "black", "isort"], check=True)


if __name__ == "__main__":
    o = ROOT / "model_lab" / "types"
    if "--out" in sys.argv:
        o = pathlib.Path(sys.argv[sys.argv.index("--out") + 1])
    generate(o)
