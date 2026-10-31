"""모델 호출 컴포넌트: 적재된 model-runtime 세션(wiring이 주입하는 바깥 자원)에 입력 하나를 넣고 InferenceResult를 낸다.

입력 InferRequest {role, input}, 출력 InferenceResult(schemas/). 서빙과 같은 경로(extract_raw / respond_raw·respond)다.
A는 raw 한 번(서빙 경로와 같은 재시도), B는 raw와 보호 적용 결과 두 가지를 모두 낸다."""
from __future__ import annotations

from ..types.InferRequest_schema import InferRequest
from ..types.InferenceResult_schema import InferenceResult


def infer(session, request: InferRequest) -> InferenceResult:
    role, inp = request["role"], request["input"]
    if role == "LORA_A":
        raw = session.extract_raw(inp)
        info = session.last_call_info
        return {"role": role, "raw_valid": bool(raw.valid), "raw_errors": list(raw.errors), "raw_output": raw.output,
                "outcome": "ok" if raw.valid else "invalid", "attempts": getattr(info, "attempts", 0), "protections": [],
                "text": None, "is_fallback": False}
    raw = session.respond_raw(inp)
    out = session.respond(inp)
    info = session.last_call_info
    return {"role": role, "raw_valid": bool(raw.valid), "raw_errors": list(raw.errors), "raw_output": raw.output,
            "outcome": getattr(info, "outcome", "ok"), "attempts": getattr(info, "attempts", 0),
            "protections": [list(p) for p in getattr(info, "protections", ())], "text": out.text, "is_fallback": bool(out.is_fallback)}
