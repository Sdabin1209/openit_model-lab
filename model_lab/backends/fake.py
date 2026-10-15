"""평가 경로 시험용 백엔드. 평가 샘플의 정답을 프롬프트로 찾아 그대로 내는 '오라클'이다.

모델을 쓰지 않으므로 모델 품질과 무관하다. 평가기·보호·보고서 경로를 CPU에서 끝까지 돌려 보는 용도다.
corrupt_every=N이면 프롬프트 해시가 N의 배수인 샘플마다 (재시도해도) 깨진 출력을 내어 재시도와 출력 보호 경로를 지나가게 한다."""
from __future__ import annotations

import hashlib
from typing import Mapping

from model_runtime import FakeBackend, GenerationRequest


def oracle_backend(prompt_to_completion: Mapping[str, str], *, corrupt_every: int = 0,
                   supported_features=()) -> FakeBackend:
    def respond(req: GenerationRequest) -> str:
        # 프롬프트 해시로 정한다: 같은 샘플은 재시도해도 계속 깨져 보호 경로까지 간다(결정적).
        if corrupt_every and int(hashlib.sha256(req.prompt.encode()).hexdigest(), 16) % corrupt_every == 0:
            return "{깨진 출력"
        return prompt_to_completion.get(req.prompt, "{}")

    b = FakeBackend(respond, supported_features=supported_features)
    return b
