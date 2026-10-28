"""contract-ready 자체 검증. 사용: <venv>/bin/python docs/change-requests/contract-ready/validate.py
- schemas/*.schema.json 을 계약 패키지의 _common.schema.json 과 함께 해석한다(복사본 없음).
- examples/valid/<이름>[.변형].json 은 통과, examples/invalid/ 는 거부되어야 한다.
- 스키마로 못 쓰는 불변식(MANIFEST note에 적은 것 중 이 문서 안에서 볼 수 있는 것)은 valid 예시에서 확인한다."""
import json
import sys
from pathlib import Path

import boundary_contracts
from boundary_contracts import canonical
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

HERE = Path(__file__).resolve().parent
CONTRACT = Path(boundary_contracts.__file__).parent / "schemas"
res = {}
for d in (CONTRACT, HERE / "schemas"):
    for p in d.glob("*.schema.json"):
        s = json.loads(p.read_text(encoding="utf-8"))
        res[p.name] = s
reg = Registry().with_resources((name, Resource.from_contents(s)) for name, s in res.items())
manifest = json.loads((HERE / "MANIFEST.json").read_text(encoding="utf-8"))
ok = bad = fail = 0
for e in manifest["entries"]:
    name = e["name"]
    v = Draft202012Validator(res[f"{name}.schema.json"], registry=reg)
    assert res[f"{name}.schema.json"]["$id"] == f"{name}.schema.json" and res[f"{name}.schema.json"]["title"] == name
    for kind in ("valid", "invalid"):
        files = sorted((HERE / "examples" / kind).glob(f"{name}.json")) + sorted((HERE / "examples" / kind).glob(f"{name}.*.json"))
        assert files, f"{kind} 예시가 없다: {name}"
        for f in files:
            errs = list(v.iter_errors(json.loads(f.read_text(encoding="utf-8"))))
            if kind == "valid":
                ok += 1
                if errs:
                    fail += 1; print("FAIL valid가 거부됨:", f.name, errs[0].message[:100])
            else:
                bad += 1
                if not errs:
                    fail += 1; print("FAIL invalid가 통과됨:", f.name)
    for f in sorted((HERE / "examples" / "valid").glob("ModelPackageLayout*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        entries = [{"path": k, "sha256": h} for k, h in sorted(d["adapter_files"].items())]
        if canonical.sha256_hash(entries) != d["adapter_hash"] or d["system_prompt"]["sha256"] != d["adapter_files"]["system_prompt.txt"]:
            fail += 1; print("FAIL 불변식 위반:", f.name)
print(f"valid 예시 {ok}건 검사, invalid 예시 {bad}건 검사, 기대와 다른 결과 {fail}건")
sys.exit(1 if fail else 0)
