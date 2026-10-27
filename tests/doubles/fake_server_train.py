"""서버 정본 학습 스크립트의 대역(시험 전용). 실제 학습은 하지 않고 인자를 받아 어댑터 모양의 파일만 쓴다.
인자 이름은 이 대역이 정한 것이며 실제 서버 스크립트의 인자 이름이 아니다."""
import argparse, json, sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--model", required=True); ap.add_argument("--data_dir", required=True); ap.add_argument("--prefix", required=True)
ap.add_argument("--out", required=True); ap.add_argument("--lr", type=float, default=0.0); ap.add_argument("--epochs", type=int, default=0)
ap.add_argument("--fail", action="store_true"); ap.add_argument("--wrong_base", action="store_true")
a = ap.parse_args()
if a.fail:
    sys.exit(3)
for s in ("train", "val"):
    assert (Path(a.data_dir) / f"{a.prefix}_{s}.jsonl").is_file()
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
(out / "adapter_model.safetensors").write_bytes(b"fake")
(out / "adapter_config.json").write_text(json.dumps({"base_model_name_or_path": "other/model" if a.wrong_base else a.model, "r": 16, "lora_alpha": 16}))
(out.parent / "args.json").write_text(json.dumps({"lr": a.lr, "epochs": a.epochs}))
