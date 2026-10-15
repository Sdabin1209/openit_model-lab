"""기반 모델 후보 비교(학습 없음). 사용: python scripts/compare_base_models.py --config configs/compare/base-model-candidates.yaml
       --schema-dir <.../schema/schemas> --out runs/base-compare [--only 이름] [--backend hf|fake]

후보마다 파서 golden 표본에 render_prompt(서빙과 같은 프롬프트)를 만들어 model-runtime의 parse_raw/parse 경로로 평가하고
EvaluationReport(지표만, 합격 판정 없음)를 쓴다. 결과 요약은 <out>/summary.json."""
from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import model_runtime  # noqa: E402
from boundary_contracts import canonical  # noqa: E402

from model_lab import metrics as M  # noqa: E402
from model_lab.evaluate import build_report  # noqa: E402
from model_lab.transform import completion_of  # noqa: E402
from tests.doubles.fixtures import load_pairs  # noqa: E402


def pick_samples(n: int) -> list[dict]:
    """파서 golden에서 결정적으로 n개. RISK_SIGNAL 사례는 모두 넣고 나머지는 균등 간격으로 고른다."""
    P = [p for p in load_pairs() if p["role"] == "PARSER"]
    risk = [p for p in P if any(i["intent"] == "RISK_SIGNAL" for i in p["expected"]["items"])]
    rest = [p for p in P if p not in risk]
    need = max(0, n - len(risk))
    step = max(1, len(rest) // max(1, need))
    chosen = risk + rest[::step][:need]
    return [{"sample_id": f"{p['case']}-t{p['turn']}", "question_set": {"id": "golden", "version": "1", "content_hash": "sha256:" + "0" * 64},
             "input": p["input"], "expected": p["expected"]} for p in chosen]


def pick_fewshot(k: int, exclude_ids: set, schema_dir: Path) -> list[tuple[str, str]]:
    """표본에 없는 파서 golden 중 서로 다른 의도의 정답 사례 k개(위험 신호 사례는 쓰지 않는다)를 (프롬프트, 정답 출력)으로."""
    P = [p for p in load_pairs() if p["role"] == "PARSER" and f"{p['case']}-t{p['turn']}" not in exclude_ids
         and not any(i["intent"] == "RISK_SIGNAL" for i in p["expected"]["items"]) and p["expected"]["items"]]
    want, out, seen = ["ANSWERED_INTENT", "CORRECTION_MARKED", "SAYS_UNKNOWN", "CONFIRM_YES"], [], set()
    for intent in want:
        for p in P:
            its = p["expected"]["items"]
            if its[0]["intent"] == intent and len(its) == 1 and intent not in seen:
                seen.add(intent)
                sc = serving("x", "1", {"temperature": 0, "seed": 0, "max_tokens": 8})
                out.append((model_runtime.render_prompt("LORA_A", p["input"], serving_config=sc, schema_dir=schema_dir),
                            completion_of("LORA_A", p["expected"])))
                break
        if len(out) >= k:
            break
    return out


def serving(backend_name: str, backend_version: str, decoding: dict) -> dict:
    return {"prompt_template_version": "prompt-2",
            "decoding": {"constraint_mode": "none", "temperature": decoding["temperature"], "seed": decoding["seed"], "max_tokens": decoding["max_tokens"]},
            "max_attempts": 1,
            "constraint": {"backend": backend_name, "backend_version": backend_version, "supported_features": [], "relax_policy_version": "relax-1"},
            "output_contract": 1, "protection_policy_version": "protect-1"}


def run_candidate(cand: dict, cfg: dict, samples: list[dict], schema_dir: Path, backend_kind: str, out: Path) -> dict:
    t0 = time.time()
    if backend_kind == "hf":
        from model_lab.backends.hf_base import HFBaseChatBackend
        fs = pick_fewshot(cfg.get("fewshot", 0), {x["sample_id"] for x in samples}, schema_dir) if cfg.get("fewshot") else []
        backend = HFBaseChatBackend({"id": cand["id"], "revision": "main"}, cfg["training"], fewshot=fs)
    else:
        from model_lab.backends.fake import oracle_backend
        sc0 = serving("fake", "1", cfg["decoding"])
        mp = {model_runtime.render_prompt("LORA_A", s["input"], serving_config=sc0, schema_dir=schema_dir): completion_of("LORA_A", s["expected"]) for s in samples}
        backend = oracle_backend(mp)
    sc = serving(backend.name, backend.version, cfg["decoding"])
    with tempfile.TemporaryDirectory() as td:
        ad = {}
        for role in ("LORA_A", "LORA_B"):   # load는 두 역할의 어댑터 경로를 요구한다. 비교는 기반 모델만 쓰므로 빈 PEFT 설정만 둔 자리표시자다
            d = Path(td) / role; d.mkdir()
            (d / "adapter_config.json").write_text(json.dumps({"peft_type": "LORA", "r": 16, "base_model_name_or_path": cand["id"]}))
            ad[role] = d
        h = model_runtime.serving_config_hash(sc)
        session = model_runtime.load(ad, {"LORA_A": sc, "LORA_B": sc}, schema_dir=schema_dir,
                                     expected_serving_config_hashes={"LORA_A": h, "LORA_B": h}, backend=backend)
        rep, pairs = build_report("LORA_A", session, samples, seen_sets={("golden", "1")}, eval_files=[], return_records=True)
    metrics, notes = M.build_metrics("LORA_A", pairs, {("golden", "1")})
    notes.append("기반 모델 그대로(미학습)이며 프롬프트를 채팅 템플릿의 사용자 메시지로 넣었다. 평가 백엔드는 transformers이고 서빙 vLLM이 아니다. "
                 f"양자화: {cfg['training']['quantization']['mode']}. 예시(few-shot) 수: {cfg.get('fewshot', 0)}.")
    elapsed = time.time() - t0
    ev = M.evaluation_report(
        role="LORA_A", report_id=f"basecmp-{cand['name']}{cfg.get('suffix', '')}", created_at="2026-10-15T00:00:00Z",
        package={"id": f"base-{cand['name']}", "version": "0", "content_hash": canonical.sha256_hash({"model": cand["id"]})},
        dataset={"id": "golden-parser-subset", "version": str(len(samples)), "content_hash": canonical.sha256_hash([s["sample_id"] for s in samples])},
        question_sets=[], backend={"name": backend.name, "version": backend.version, "constrained_decoding": False},
        decoding=cfg["decoding"], metrics=metrics, notes=notes, detail={"overall": rep["overall"], "failures": rep["failures"]})
    (out / f"{cand['name']}{cfg.get('suffix', '')}.evaluation-report.json").write_text(json.dumps(ev, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    top = {m["name"]: (round(m["value"], 4), m["n"]) for m in metrics if "slice" not in m}
    res = {"name": cand["name"] + cfg.get("suffix", ""), "id": cand["id"], "metrics": top, "seconds": round(elapsed, 1), "samples": len(samples)}
    if backend_kind == "hf":
        res.update(peak_cuda_mb=backend.peak_mb(), generated=backend.generated, new_tokens=backend.new_tokens)
        del backend
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True); ap.add_argument("--schema-dir", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--only"); ap.add_argument("--backend", choices=["hf", "fake"], default="hf")
    a = ap.parse_args()
    cfg = yaml.safe_load(Path(a.config).read_text())
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    samples = pick_samples(cfg["samples"])
    results = []
    for cand in cfg["candidates"]:
        if a.only and cand["name"] != a.only:
            continue
        try:
            r = run_candidate(cand, cfg, samples, Path(a.schema_dir), a.backend, out)
        except Exception as e:   # 후보 하나의 실패(메모리 부족 등)가 나머지를 막지 않는다. 사유만 남긴다(원문 없음)
            r = {"name": cand["name"] + cfg.get("suffix", ""), "id": cand["id"], "error": f"{type(e).__name__}: {str(e)[:200]}"}
        print(json.dumps(r, ensure_ascii=False), flush=True)
        results.append(r)
        sp = out / "summary.json"
        old = {x["name"]: x for x in (json.loads(sp.read_text()) if sp.exists() else [])}
        old[r["name"]] = r
        sp.write_text(json.dumps(list(old.values()), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
