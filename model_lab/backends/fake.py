"""평가 경로 시험용 백엔드. 평가 샘플의 정답을 프롬프트로 찾아 그대로 내는 '오라클'이다.

모델을 쓰지 않으므로 모델 품질과 무관하다. 평가기·보호·보고서 경로를 CPU에서 끝까지 돌려 보는 용도다.
corrupt_every=N이면 N번째 호출마다 깨진 출력을 내어 재시도와 출력 보호 경로를 지나가게 한다."""
from __future__ import annotations

from typing import Mapping

from model_runtime import FakeBackend, GenerationRequest


def oracle_backend(prompt_to_completion: Mapping[str, str], *, corrupt_every: int = 0,
                   supported_features=()) -> FakeBackend:
    calls = {"n": 0}

    def respond(req: GenerationRequest) -> str:
        calls["n"] += 1
        if corrupt_every and calls["n"] % corrupt_every == 0:
            return "{깨진 출력"
        return prompt_to_completion.get(req.prompt, "{}")

    b = FakeBackend(respond, supported_features=supported_features)
    return b
