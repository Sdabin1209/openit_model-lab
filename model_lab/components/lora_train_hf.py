"""Hugging Face transformers + peft LoRA 학습기(torch는 이 파일 안에서만 가져온다).

경고(D78, G06): 이 파일은 학습의 정본이 아니다. 정본은 학습 서버의 코드(train_lora_a.py, train_lora_b.py)다.
이 사본은 서버 코드와 다르다(prompt/completion을 따로 토큰화하고 채팅 템플릿을 쓰지 않음, 4비트 양자화 선택, gpu-dev 프로파일의
모듈 구성). 여기서 만든 어댑터는 서버 학습과 같지 않다. 파이프라인 검증(12GB 호스트의 통과 시험)용으로만 남긴다.
실제 학습은 `python -m model_lab train --config configs/train.yaml`(서버 정본을 감싼다)을 쓴다.

- 기반 모델, 양자화, 최대 길이, 배치, 학습률, 에폭, GPU 수, 메모리 비율은 모두 training 설정이다.
- 손실은 완성(completion) 토큰에만 건다. 최대 길이를 넘는 샘플은 건너뛰고 건수를 기록한다.
- 데이터 순서는 (시드, 에폭)으로 결정되고 체크포인트(어댑터+옵티마이저+스텝)에서 이어서 한다.
"""
from __future__ import annotations

import json
import math
import os
import random
import shutil
from importlib import metadata
from pathlib import Path

from ..types.TrainRequest_schema import TrainRequest
from ..types.TrainResult_schema import TrainResult
from ..wiring.errors import TrainerUnavailable


def read_jsonl(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def _paths(req) -> dict:
    """TrainRequest(schemas/)의 경로 필드를 Path로 바꾼다. 값 검증은 호출하는 wiring이 스키마로 한다."""
    return {**req, **{k: Path(req[k]) for k in ("train_path", "val_path", "adapter_dir", "work_dir") if req.get(k) is not None}}

_DTYPES = {"float16": "float16", "bfloat16": "bfloat16", "float32": "float32"}


def lib_versions() -> dict:
    out = {}
    for n in ("torch", "transformers", "peft", "accelerate", "bitsandbytes"):
        try:
            out[n] = metadata.version(n)
        except metadata.PackageNotFoundError:
            pass
    return out


def _import():
    try:
        import torch, transformers, peft  # noqa: F401
        return torch, transformers, peft
    except ImportError as e:
        raise TrainerUnavailable(f"학습 환경이 없다({e.name}). requirements-train.txt를 설치한다.") from None


def load_base(torch, transformers, base_model: dict, training: dict):
    """토크나이저와 기반 모델. 양자화는 training.quantization으로 정한다."""
    q = training.get("quantization", {"mode": "none"})
    dtype = getattr(torch, training.get("dtype", "bfloat16"))
    rev = base_model.get("revision")
    kw = {"torch_dtype": dtype}
    if rev and rev != "main":
        kw["revision"] = rev
    elif rev == "main":
        kw["revision"] = "main"
    if q.get("mode", "none") != "none":
        cd = getattr(torch, q.get("compute_dtype", "bfloat16"))
        if q["mode"] == "4bit-nf4":
            kw["quantization_config"] = transformers.BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=cd, bnb_4bit_use_double_quant=True)
        else:
            kw["quantization_config"] = transformers.BitsAndBytesConfig(load_in_8bit=True)
    gpus = training.get("gpu_count", 1)
    if training.get("attn_implementation"):
        kw["attn_implementation"] = training["attn_implementation"]
    kw["device_map"] = "auto" if gpus > 1 else {"": 0}
    tok = transformers.AutoTokenizer.from_pretrained(base_model["id"], revision=rev if rev else None)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = transformers.AutoModelForCausalLM.from_pretrained(base_model["id"], **kw)
    return tok, model


def resolved_revision(model, base_model: dict, cached_commit=None) -> str:
    """실제로 쓴 기반 모델의 커밋. huggingface_hub 공개 API가 알려 주는 캐시 스냅샷의 커밋을 쓴다(없으면 요청한 revision, T24).
    cached_commit은 hf_cache_lookup 컴포넌트의 함수를 wiring이 주입한다(컴포넌트끼리 import하지 않는다)."""
    try:
        h = cached_commit(base_model["id"], base_model.get("revision")) if cached_commit else None
    except ImportError:
        h = None
    return h or base_model.get("revision", "unknown")


class HFLoraTrainer:
    name = "hf"

    def __init__(self, cached_commit=None):
        self.cached_commit = cached_commit

    def train(self, req: TrainRequest) -> TrainResult:
        req = _paths(req)
        torch, transformers, peft = _import()
        t = req["training"]
        if not torch.cuda.is_available():
            raise TrainerUnavailable("CUDA GPU가 없다")
        if t.get("deterministic", True):
            # 같은 시드 -> 같은 어댑터에 가깝게(완전한 비트 일치는 하드웨어·커널에 따라 보장되지 않는다)
            os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
            torch.use_deterministic_algorithms(True, warn_only=True)
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False
        frac = t.get("gpu_memory_fraction")
        if frac:
            for i in range(t.get("gpu_count", 1)):
                torch.cuda.set_per_process_memory_fraction(frac, i)
        random.seed(req["seed"])
        torch.manual_seed(req["seed"])
        tok, model = load_base(torch, transformers, req["base_model"], t)
        if t.get("quantization", {}).get("mode", "none") != "none":
            model = peft.prepare_model_for_kbit_training(model, use_gradient_checkpointing=t.get("gradient_checkpointing", True))
        elif t.get("gradient_checkpointing", True):
            model.gradient_checkpointing_enable()
            model.enable_input_require_grads()
        lc = t.get("lora", {})
        cfg = peft.LoraConfig(r=lc.get("r", 16), lora_alpha=lc.get("alpha", 32), lora_dropout=lc.get("dropout", 0.05),
                              target_modules=lc.get("target_modules", ["q_proj", "k_proj", "v_proj", "o_proj"]),
                              task_type="CAUSAL_LM")
        rev_used = resolved_revision(model, req["base_model"], self.cached_commit)
        model = peft.get_peft_model(model, cfg)

        max_len = t.get("max_seq_len", 1024)
        rows = read_jsonl(req["train_path"])
        if t.get("max_train_samples"):
            rows = rows[: t["max_train_samples"]]
        data, skipped = [], 0
        for r in rows:
            p = tok(r["prompt"], add_special_tokens=False)["input_ids"]
            c = tok(r["completion"], add_special_tokens=False)["input_ids"] + [tok.eos_token_id]
            if len(p) + len(c) > max_len:
                skipped += 1
                continue
            data.append((p + c, [-100] * len(p) + c))
        if not data:
            raise TrainerUnavailable("최대 길이 안에 들어가는 학습 샘플이 없다")

        bs, ga = t.get("per_device_batch_size", 1), t.get("grad_accum_steps", 1)
        per_step = bs * ga
        epochs = t.get("epochs", 1)
        total = max(1, math.ceil(len(data) * epochs / per_step))
        if t.get("max_steps"):
            total = min(total, t["max_steps"])
        order: list[int] = []
        ep = 0
        while len(order) < total * per_step:
            idx = list(range(len(data)))
            random.Random(req["seed"] * 1009 + ep).shuffle(idx)
            order += idx
            ep += 1

        params = [p for p in model.parameters() if p.requires_grad]
        opt = torch.optim.AdamW(params, lr=t.get("learning_rate", 2e-4), weight_decay=0.0)
        warm = int(total * t.get("warmup_ratio", 0.03))
        sched = torch.optim.lr_scheduler.LambdaLR(
            opt, lambda s: (s + 1) / max(1, warm) if s < warm else max(0.0, (total - s) / max(1, total - warm)))

        ck_dir = req["work_dir"] / "ckpt"
        step, resumed = 0, 0
        if (ck_dir / "state.json").exists():
            st = json.loads((ck_dir / "state.json").read_text())
            peft.set_peft_model_state_dict(model, _load_adapter_weights(ck_dir))
            opt.load_state_dict(torch.load(ck_dir / "optimizer.pt", map_location="cpu"))
            sched.load_state_dict(torch.load(ck_dir / "scheduler.pt", map_location="cpu"))
            step = resumed = st["step"]
            print(f"[hf] 체크포인트에서 재개: step {step}/{total}", flush=True)

        dev = next(model.parameters()).device
        model.train()
        every, log_every = t.get("checkpoint_every_steps", 50), t.get("log_every_steps", 10)
        req["work_dir"].mkdir(parents=True, exist_ok=True)
        mlog = open(req["work_dir"] / "metrics.jsonl", "a", encoding="utf-8")
        last_loss = None
        while step < total:
            loss_acc = 0.0
            for m in range(ga):
                base = (step * per_step) + m * bs
                batch = [data[order[base + j]] for j in range(bs)]
                L = max(len(x[0]) for x in batch)
                ids = torch.tensor([x[0] + [tok.pad_token_id] * (L - len(x[0])) for x in batch], device=dev)
                lab = torch.tensor([x[1] + [-100] * (L - len(x[1])) for x in batch], device=dev)
                att = torch.tensor([[1] * len(x[0]) + [0] * (L - len(x[0])) for x in batch], device=dev)
                loss = model(input_ids=ids, attention_mask=att, labels=lab).loss / ga
                loss.backward()
                loss_acc += float(loss)
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            opt.step(); sched.step(); opt.zero_grad(set_to_none=True)
            step += 1
            last_loss = loss_acc
            if step % log_every == 0 or step == total:
                mlog.write(json.dumps({"step": step, "loss": loss_acc, "lr": sched.get_last_lr()[0]}) + "\n"); mlog.flush()
                print(f"[hf] step {step}/{total} loss {loss_acc:.4f}", flush=True)
            if step % every == 0 and step < total:
                _save_ckpt(torch, peft, model, opt, sched, ck_dir, step)
        mlog.close()
        if req["adapter_dir"].exists():
            shutil.rmtree(req["adapter_dir"])
        model.save_pretrained(str(req["adapter_dir"]), safe_serialization=True)
        readme = req["adapter_dir"] / "README.md"   # 어댑터 해시에는 어댑터 파일만 들어간다
        if readme.exists():
            readme.unlink()
        return {"steps": total, "resumed_from_step": resumed, "samples_used": len(data), "samples_skipped_too_long": skipped,
                "base_model_revision": rev_used, "library_versions": lib_versions(),
                "metrics": {"last_step_loss": last_loss, "peak_cuda_mem_mb": int(torch.cuda.max_memory_allocated() / 2**20)}}


def _save_ckpt(torch, peft, model, opt, sched, ck_dir: Path, step: int) -> None:
    tmp = ck_dir.with_name(ck_dir.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    model.save_pretrained(str(tmp), safe_serialization=True)
    torch.save(opt.state_dict(), tmp / "optimizer.pt")
    torch.save(sched.state_dict(), tmp / "scheduler.pt")
    (tmp / "state.json").write_text(json.dumps({"step": step}))     # 마지막에 쓴다: 있어야 완전한 체크포인트
    if ck_dir.exists():
        shutil.rmtree(ck_dir)
    os.replace(tmp, ck_dir)


def _load_adapter_weights(ck_dir: Path):
    from safetensors.torch import load_file
    return load_file(str(ck_dir / "adapter_model.safetensors"))
