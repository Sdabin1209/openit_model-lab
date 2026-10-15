"""golden 입출력 쌍으로 DatasetManifest + JSONL 파일을 만든다(시험·파이프라인 시험용).

golden은 요구 작성자가 만든 정적 예시다. 여기서는 파이프라인을 끝까지 돌려 보는 재료로만 쓰고 품질 판단에 쓰지 않는다.
분할: 사례 ID로 결정(해시 기반). 문항 세트: 학습·검증은 qs-train, 평가의 일부는 qs-unseen(보지 못한 문항 세트 시험)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from boundary_contracts import canonical

GOLDEN = Path(__file__).resolve().parents[1] / "golden"
ROLE_MAP = {"UtteranceParserInput": "PARSER", "ResponseGeneratorInput": "GENERATOR"}


def qs_ref(name: str, version: str = "1") -> dict:
    return {"id": name, "version": version, "content_hash": "sha256:" + hashlib.sha256(f"{name}@{version}".encode()).hexdigest()}


def split_of(case: str) -> str:
    h = int(hashlib.sha256(case.encode()).hexdigest(), 16) % 10
    return "EVAL" if h < 2 else ("VALIDATION" if h < 3 else "TRAIN")


def load_pairs(limit_cases: int | None = None) -> list[dict]:
    """[{case, role(PARSER|GENERATOR), turn, input, expected}]"""
    out = []
    files = sorted(GOLDEN.glob("*.json"))
    for f in files[:limit_cases]:
        d = json.loads(f.read_text(encoding="utf-8"))
        by_turn: dict = {}
        for s in d["specimens"]:
            if s["role"] == "representation":
                continue
            by_turn.setdefault((s["turn"], ROLE_MAP.get(s["contract"]) or ("PARSER" if s["contract"] == "ParserOutput" else "GENERATOR")), {})[s["role"]] = s["data"]
        for (turn, role), v in sorted(by_turn.items()):
            if "input" in v and "output" in v:
                out.append({"case": d["case"], "role": role, "turn": turn, "input": v["input"], "expected": v["output"]})
    return out


def build_dataset(dest: Path, dataset_id: str, version: str, *, max_cases: int | None = None, unseen_eval: bool = True) -> dict:
    """dest/<dataset_id>/<version>/ 에 파일과 manifest.json을 쓰고 매니페스트를 낸다."""
    root = dest / dataset_id / version
    root.mkdir(parents=True, exist_ok=True)
    pairs = load_pairs(max_cases)
    qs_train, qs_un = qs_ref("qs-train"), qs_ref("qs-unseen")
    rows: dict = {}
    for p in pairs:
        split = split_of(p["case"])
        qs = qs_un if (split == "EVAL" and unseen_eval and int(hashlib.sha256(p["case"].encode()).hexdigest(), 16) % 2) else qs_train
        sample = {"pipeline_schema_version": "1.1", "sample_id": f"{p['case']}-t{p['turn']}-{p['role'][0]}".lower(),
                  "scenario_id": f"scn-{p['case']}".lower(), "question_set": qs, "runtime_contract_version": 1,
                  "split": split, "origin": "SYNTHETIC", "input": p["input"], "expected": p["expected"]}
        if p["role"] == "GENERATOR":
            sample["style_guide_version"] = "style-1"
        rows.setdefault((p["role"], split), []).append(sample)
    files = []
    for (role, split), ss in sorted(rows.items()):
        name = f"{role.lower()}-{split.lower()}.jsonl"
        with open(root / name, "w", encoding="utf-8") as fh:
            for s in ss:
                fh.write(json.dumps(s, ensure_ascii=False, sort_keys=True) + "\n")
        qsets = sorted({(s["question_set"]["id"], s["question_set"]["version"]): s["question_set"] for s in ss}.values(), key=lambda r: r["id"])
        files.append({"path": name, "role": role, "split": split, "sample_count": len(ss),
                      "content_hash": canonical.file_hash(root / name), "question_sets": qsets})
    m = {"pipeline_schema_version": "1.1", "dataset_id": dataset_id, "version": version,
         "created_at": "2026-10-15T00:00:00Z", "runtime_contract_version": 1, "question_sets": [qs_train, qs_un],
         "generator_version": "fixture-golden-1", "seed_case_ids": [], "turn_engine_version": "0.2.0",
         "scenario_disjoint_across_splits": True, "files": files}
    m["content_hash"] = canonical.content_hash(m)
    (root / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    vp = dest / dataset_id / "versions.json"
    vs = json.loads(vp.read_text()) if vp.exists() else []
    if version not in vs:
        vs.append(version)
    vp.write_text(json.dumps(vs))
    return m
