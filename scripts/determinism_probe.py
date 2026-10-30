"""같은 학습을 두 번 돌려 어댑터 해시가 같은지 본다(GPU 호스트용 점검). 사용: python scripts/determinism_probe.py <runs 디렉터리 내 train.jsonl> [quant]"""
import json, shutil, sys, tempfile
from pathlib import Path
from boundary_contracts import canonical
from model_lab.components.lora_train_hf import HFLoraTrainer

train, quant = Path(sys.argv[1]), (sys.argv[2] if len(sys.argv) > 2 else "none")
tr = {"quantization": {"mode": quant, "compute_dtype": "bfloat16"}, "dtype": "bfloat16", "max_seq_len": 4096, "per_device_batch_size": 1,
      "grad_accum_steps": 2, "learning_rate": 0.0002, "epochs": 1, "max_steps": 3, "max_train_samples": 6, "gpu_memory_fraction": 0.85,
      "gradient_checkpointing": True, "checkpoint_every_steps": 100, "log_every_steps": 1}
if len(sys.argv) > 3:
    tr["attn_implementation"] = sys.argv[3]
hs = []
for i in range(2):
    d = Path(tempfile.mkdtemp())
    HFLoraTrainer().train({"role": "LORA_B", "base_model": {"id": "Qwen/Qwen2.5-1.5B-Instruct", "revision": "main"}, "training": tr, "seed": 7, "train_path": str(train), "val_path": None, "adapter_dir": str(d / "a"), "work_dir": str(d / "w")})
    hs.append(canonical.directory_hash(d / "a")); shutil.rmtree(d)
print("PROBE", quant, hs[0] == hs[1], hs)
