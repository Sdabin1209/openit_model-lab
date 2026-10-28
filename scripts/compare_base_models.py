"""기반 모델 후보 비교(학습 없음). 사용: python scripts/compare_base_models.py --config configs/compare/base-model-candidates.yaml
       --schema-dir <.../schema/schemas> --out runs/base-compare [--only 이름] [--backend hf|fake]

후보마다 LoRA-A 표본에 render_prompt(서빙과 같은 프롬프트, prompt-4)를 만들어 model-runtime(버전은 `requirements.txt`)의 extract_raw 경로로 평가하고
EvaluationReport(지표만, 합격 판정 없음)를 쓴다. 결과 요약은 <out>/summary.json.

2.0.0(D74): v1 golden은 더 쓰지 않는다. 표본은 --samples(ActionExtractionSample JSONL, 예: transform.read_server_file로 만든 것)이고
없으면 tests/doubles/fixtures.py의 합성 표본이다(경로 시험용이며 품질 비교에 쓰지 않는다). 시스템 프롬프트는 --system-prompt-file
(기본 prompts/lora_a.system.txt). render_prompt가 이미 채팅 형식 문자열이므로 hf 백엔드는 채팅 템플릿을 한 번 더 씌우지 않는다.
few-shot은 v2로 옮기지 않았다(hf 백엔드에서 fewshot 설정이 있으면 중단)."""
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
from model_lab.config import load_yaml12  # noqa: E402
from model_lab.evaluate import build_report  # noqa: E402
from model_lab.transform import render_output  # noqa: E402
from tests.doubles.fixtures import load_samples  # noqa: E402


def hf_cache_dir() -> Path:
    import os
    return Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"


def _tree_bytes(path: Path) -> int:
    """심볼릭 링크를 따라가지 않고 파일 크기를 합한다(du와 같은 방식, 공유 blob 구조에서 이중 계산을 피한다)."""
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def cache_gb() -> float:
    """HF 캐시의 실제 디스크 사용량(GB). huggingface_hub.scan_cache_dir()를 쓰고 없으면 파일 크기 합."""
    d = hf_cache_dir()
    try:
        from huggingface_hub import scan_cache_dir
        return scan_cache_dir(d).size_on_disk / 2**30
    except Exception:
        return _tree_bytes(d) / 2**30


def work_gb() -> float:
    """~/openit-gpu(작업 폴더 전체: 가상환경, runs, 데이터)의 크기(GB)."""
    return _tree_bytes(Path.home() / "openit-gpu") / 2**30


def model_cached(cand: dict) -> bool:
    d = hf_cache_dir() / ("models--" + cand["id"].replace("/", "--"))
    return d.exists() and not list(d.rglob("*.incomplete")) and any((d / "snapshots").glob("*")) if d.exists() else False


def footprint_guard(cand: dict, max_footprint_gb: float) -> None:
    """내려받기 전에 우리 작업의 사용량 예산을 확인한다: (HF 캐시 + ~/openit-gpu + 후보 예상 크기)가 예산을 넘으면 멈춘다.
    WSL의 df는 가상 디스크 크기만 보여 주고 호스트 드라이브의 실제 여유를 알려 주지 않으므로 df를 쓰지 않는다.
    이미 완전히 받은 후보는 캐시 크기에 들어 있으므로 예상 크기를 더하지 않는다. 자동 삭제는 하지 않는다."""
    add = 0.0 if model_cached(cand) else float(cand.get("approx_gb", 20))
    cache, work = cache_gb(), work_gb()
    if cache + work + add > float(max_footprint_gb):
        raise SystemExit(f"사용량 예산 초과: HF 캐시 {cache:.1f}GB + 작업 폴더 {work:.1f}GB + {cand['id']} 예상 {add:.0f}GB > 예산 {max_footprint_gb}GB. "
                         "scripts/clean_model_cache.py로 정리 후보를 확인하라(자동 삭제하지 않는다).")


def pick_samples(n: int, path: Path | None = None) -> list[dict]:
    """LoRA-A 표본에서 결정적으로 n개(균등 간격). path가 없으면 시험용 합성 표본. question_set이 없으면 'compare@1'로 채운다."""
    if path:
        from model_lab.datasets import read_samples
        P = list(read_samples(Path(path), "LORA_A"))
    else:
        P = load_samples("LORA_A")
    step = max(1, len(P) // max(1, n))
    return [{**p, "question_set": p.get("question_set") or {"id": "compare", "version": "1", "content_hash": "sha256:" + "0" * 64}}
            for p in P[::step][:n]]


def serving(backend_name: str, backend_version: str, decoding: dict) -> dict:
    return {"prompt_template_version": "prompt-4",
            "decoding": {"constraint_mode": "none", "temperature": decoding["temperature"], "seed": decoding["seed"], "max_tokens": decoding["max_tokens"]},
            "max_attempts": 1,
            "constraint": {"backend": backend_name, "backend_version": backend_version, "supported_features": [], "relax_policy_version": "relax-3"},
            "output_contract": 1, "protection_policy_version": "protect-2"}


def run_candidate(cand: dict, cfg: dict, samples: list[dict], schema_dir: Path, backend_kind: str, out: Path, system_prompt: str) -> dict:
    t0 = time.time()
    if backend_kind == "hf":
        from model_lab.backends.hf_base import HFBaseChatBackend
        if cfg.get("fewshot"):
            raise SystemExit("few-shot은 v2로 옮기지 않았다(설정의 fewshot을 0으로)")
        backend = HFBaseChatBackend({"id": cand["id"], "revision": "main"}, cfg["training"], use_chat_template=False)
    else:
        from model_lab.backends.fake import oracle_backend
        sc0 = serving("fake", "1", cfg["decoding"])
        mp = {model_runtime.render_prompt("LORA_A", s["input"], serving_config=sc0, system_prompt=system_prompt, schema_dir=schema_dir): render_output("LORA_A", s["expected"]) for s in samples}
        backend = oracle_backend(mp)
    sc = serving(backend.name, backend.version, cfg["decoding"])
    with tempfile.TemporaryDirectory() as td:
        ad = {}
        for role in ("LORA_A", "LORA_B"):   # load는 두 역할의 어댑터 경로를 요구한다. 비교는 기반 모델만 쓰므로 빈 PEFT 설정만 둔 자리표시자다
            d = Path(td) / role; d.mkdir()
            (d / "adapter_config.json").write_text(json.dumps({"peft_type": "LORA", "r": 16, "base_model_name_or_path": cand["id"]}))
            (d / "system_prompt.txt").write_bytes(system_prompt.encode("utf-8"))
            ad[role] = d
        h = model_runtime.serving_config_hash(sc)
        sp = {r: {"path": "system_prompt.txt", "sha256": canonical.file_hash(ad[r] / "system_prompt.txt")} for r in ad}
        session = model_runtime.load(ad, {"LORA_A": sc, "LORA_B": sc}, schema_dir=schema_dir, system_prompts=sp,
                                     expected_serving_config_hashes={"LORA_A": h, "LORA_B": h}, backend=backend)
        rep, pairs = build_report("LORA_A", session, samples, seen_sets={("compare", "1")}, eval_files=[], return_records=True)
    metrics, notes = M.build_metrics("LORA_A", pairs, {("compare", "1")})
    metrics, unregistered = M.split_by_registry(metrics)      # 계약 레지스트리에 없는 이름은 최상위에 내지 않는다(ML-01)
    notes.append("기반 모델 그대로(미학습)이며 프롬프트를 채팅 템플릿의 사용자 메시지로 넣었다. 평가 백엔드는 transformers이고 서빙 vLLM이 아니다. "
                 f"양자화: {cfg['training']['quantization']['mode']}. 예시(few-shot) 수: {cfg.get('fewshot', 0)}.")
    elapsed = time.time() - t0
    ev = M.evaluation_report(
        role="LORA_A", report_id=f"basecmp-{cand['name']}{cfg.get('suffix', '')}", created_at="2026-10-15T00:00:00Z",
        package={"id": f"base-{cand['name']}", "version": "0", "content_hash": canonical.sha256_hash({"model": cand["id"]})},
        dataset={"id": "lora-a-subset", "version": str(len(samples)), "content_hash": canonical.sha256_hash([s["sample_id"] for s in samples])},
        question_sets=[], backend={"name": backend.name, "version": backend.version, "constrained_decoding": False},
        decoding=cfg["decoding"], metrics=metrics, notes=notes, detail={"overall": rep["overall"], "failures": rep["failures"], "unregistered_metrics": unregistered})
    (out / f"{cand['name']}{cfg.get('suffix', '')}.evaluation-report.json").write_text(json.dumps(ev, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    top = {m["name"]: (round(m["value"], 4), m["n"]) for m in metrics if "slice" not in m}
    res = {"name": cand["name"] + cfg.get("suffix", ""), "id": cand["id"], "metrics": top, "seconds": round(elapsed, 1), "samples": len(samples)}
    if backend_kind == "hf":
        res.update(peak_cuda_mb=backend.peak_mb(), generated=backend.generated, new_tokens=backend.new_tokens)
        del backend
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True); ap.add_argument("--schema-dir", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--only"); ap.add_argument("--backend", choices=["hf", "fake"], default="hf")
    ap.add_argument("--samples", help="ActionExtractionSample JSONL(없으면 시험용 합성 표본)")
    ap.add_argument("--system-prompt-file", default=str(Path(__file__).resolve().parents[1] / "prompts" / "lora_a.system.txt"))
    a = ap.parse_args(argv)
    cfg = load_yaml12(Path(a.config).read_text())
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    samples = pick_samples(cfg["samples"], Path(a.samples) if a.samples else None)
    system_prompt = Path(a.system_prompt_file).read_bytes().decode("utf-8")
    results = []
    for cand in cfg["candidates"]:
        if a.only and cand["name"] != a.only:
            continue
        if a.backend == "hf":   # 가짜 백엔드는 모델을 내려받지 않으므로 디스크 예산 가드를 적용하지 않는다(호스트 디스크 상태와 무관해야 한다)
            footprint_guard(cand, cfg.get("max_footprint_gb", 20))      # 예산을 넘으면 이 후보 전에 멈춘다(후보 하나의 실패로 삼키지 않는다)
        try:
            r = run_candidate(cand, cfg, samples, Path(a.schema_dir), a.backend, out, system_prompt)
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
