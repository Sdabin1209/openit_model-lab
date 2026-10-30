"""model-runtime의 ConstraintBackend를 구현하는 transformers+peft 백엔드.

제약 디코딩은 하지 않는다(constraint_mode=none). 출력 형식은 model-runtime의 사후 검증·재시도·보호가 맡는다.
name/version은 serving_config.constraint의 backend/backend_version과 일치해야 한다. version은 이 래퍼의 버전이고,
실제 torch/transformers/peft 버전은 run 기록에 남긴다."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from ..wiring.errors import TrainerUnavailable

BACKEND_NAME = "hf-transformers"
BACKEND_VERSION = "1"


class HFAdapterBackend:
    name = BACKEND_NAME
    version = BACKEND_VERSION
    supported_features = frozenset()

    def __init__(self, base_model: dict, adapters: Mapping[str, Path], training: dict, *, import_libs, load_base):
        # import_libs·load_base는 lora_train_hf의 함수를 wiring이 주입한다(컴포넌트끼리 import하지 않는다)
        torch, transformers, peft = import_libs()
        if not torch.cuda.is_available():
            raise TrainerUnavailable("CUDA GPU가 없다")
        self._torch = torch
        frac = training.get("gpu_memory_fraction")
        if frac:
            torch.cuda.set_per_process_memory_fraction(frac, 0)
        self.tok, base = load_base(torch, transformers, base_model, training, for_training=False)
        roles = sorted(adapters)
        self.model = peft.PeftModel.from_pretrained(base, str(adapters[roles[0]]), adapter_name=roles[0])
        for r in roles[1:]:
            self.model.load_adapter(str(adapters[r]), adapter_name=r)
        self.model.eval()
        self._active = None

    def generate(self, request) -> str:
        torch = self._torch
        if self._active != request.role:
            self.model.set_adapter(request.role)
            self._active = request.role
        d = request.decoding
        ids = self.tok(request.prompt, return_tensors="pt", add_special_tokens=False).to(self.model.device)
        torch.manual_seed(request.seed)
        temp = float(d.get("temperature", 0))
        kw = {"max_new_tokens": int(d["max_tokens"]), "pad_token_id": self.tok.pad_token_id, "do_sample": temp > 0}
        if temp > 0:
            kw["temperature"] = temp
            if "top_p" in d:
                kw["top_p"] = float(d["top_p"])
        with torch.no_grad():
            out = self.model.generate(**ids, **kw)
        return self.tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)
