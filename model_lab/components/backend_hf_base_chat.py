"""기반 모델 비교용 백엔드: 어댑터 없이 기반 모델만, 채팅 템플릿에 프롬프트를 사용자 메시지로 넣어 생성한다.

학습하지 않은 기반 모델의 한국어·JSON 출력 능력을 같은 프롬프트·온도 0으로 재는 용도다(서빙 경로가 아니다).
*_raw와 보호 적용 호출이 같은 프롬프트·시드를 두 번 부르므로 결과를 캐시해 생성은 한 번만 한다."""
from __future__ import annotations

from ..wiring.errors import TrainerUnavailable

BACKEND_NAME = "hf-base-chat"
BACKEND_VERSION = "1"


class HFBaseChatBackend:
    name = BACKEND_NAME
    version = BACKEND_VERSION
    supported_features = frozenset()

    def __init__(self, base_model: dict, training: dict, *, import_libs, load_base, use_chat_template: bool = True, fewshot: list | None = None):
        torch, transformers, _ = import_libs()
        if not torch.cuda.is_available():
            raise TrainerUnavailable("CUDA GPU가 없다")
        self._torch = torch
        frac = training.get("gpu_memory_fraction")
        if frac:
            torch.cuda.set_per_process_memory_fraction(frac, 0)
        torch.cuda.reset_peak_memory_stats()
        self.tok, self.model = load_base(torch, transformers, base_model, training)
        self.model.eval()
        self.use_chat_template = use_chat_template
        self.fewshot = fewshot or []     # [(프롬프트, 정답 출력)] 앞선 대화 턴으로 넣는다
        self._cache: dict = {}
        self.generated = 0
        self.new_tokens = 0

    def peak_mb(self) -> int:
        return int(self._torch.cuda.max_memory_allocated() / 2**20)

    def _encode(self, prompt: str):
        if self.use_chat_template and getattr(self.tok, "chat_template", None):
            msgs = []
            for u, a in self.fewshot:
                msgs += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
            msgs.append({"role": "user", "content": prompt})
            try:   # Qwen3: 추론(thinking) 모드를 끈다
                text = self.tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
            except TypeError:
                text = self.tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
            return self.tok(text, return_tensors="pt", add_special_tokens=False)
        return self.tok(prompt, return_tensors="pt", add_special_tokens=False)

    def generate(self, request) -> str:
        key = (request.role, request.prompt, request.seed, request.attempt)
        if key in self._cache:
            return self._cache[key]
        torch = self._torch
        d = request.decoding
        ids = self._encode(request.prompt).to(self.model.device)
        torch.manual_seed(request.seed)
        temp = float(d.get("temperature", 0))
        kw = {"max_new_tokens": int(d["max_tokens"]), "pad_token_id": self.tok.pad_token_id, "do_sample": temp > 0}
        if temp > 0:
            kw["temperature"] = temp
        with torch.no_grad():
            out = self.model.generate(**ids, **kw)
        new = out[0][ids["input_ids"].shape[1]:]
        self.generated += 1
        self.new_tokens += int(new.shape[0])
        text = self.tok.decode(new, skip_special_tokens=True)
        self._cache[key] = text
        return text
