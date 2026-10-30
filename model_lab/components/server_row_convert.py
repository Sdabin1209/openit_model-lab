"""학습 서버 데이터셋 줄 변환 컴포넌트: 서버의 messages 형식 줄 <-> 계약 ActionExtractionSample / ResponseSample (순수 변환, 파일 읽기 포함).

입력 ServerDatasetRow(schemas/) 또는 샘플, 출력 샘플 또는 ServerDatasetRow. 서버 줄 형식 검증은 스키마가 맡고(여기서 손 검사하지 않는다),
2.0.0(D74)의 변환 규칙은 아래 주석에 있다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Mapping

from boundary_contracts import PIPELINE_SCHEMA_VERSION, canonical, validator

from ..wiring import schemas
from ..wiring.errors import ConversionError

SERVER_SPLIT = {"train": "TRAIN", "val": "VALIDATION", "test": "EVAL"}
SAMPLE_SPLIT = {v: k for k, v in SERVER_SPLIT.items()}
SAMPLE_CONTRACT_V2 = {"LORA_A": "ActionExtractionSample", "LORA_B": "ResponseSample"}
_ROLES = ("LORA_A", "LORA_B")


def _check_role(role: str) -> None:
    if role not in _ROLES:
        raise ConversionError(f"알 수 없는 역할: {role}")


def render_input(role: str, inp: Mapping) -> str:
    """모델 입력 JSON을 서버 학습 데이터와 같은 글자로 만든다(확인: 서버 LoRA-A 5,077줄, LoRA-B 5,187줄 전부 일치).

    최상위 키는 정렬한다. LoRA-A의 state는 **주어진 순서를 보존**하고(서버 데이터에서 정렬되어 있지 않다),
    state의 각 값은 value, status 순서다. 한글은 그대로 쓴다(ensure_ascii=False)."""
    _check_role(role)
    out: dict = {}
    for k in sorted(inp):
        v = inp[k]
        if role == "LORA_A" and k == "state":
            v = {name: {"value": e["value"], "status": e["status"]} for name, e in v.items()}
        out[k] = v
    return json.dumps(out, ensure_ascii=False, sort_keys=(role == "LORA_B"))


def render_output(role: str, expected) -> str:
    """정답 -> assistant 글자. LoRA-A는 키를 정렬한 JSON, LoRA-B는 문장 그대로(JSON이 아니다)."""
    _check_role(role)
    if role == "LORA_B":
        if not isinstance(expected, str):
            raise ConversionError("LoRA-B의 정답은 문장(문자열)이어야 한다")
        return expected
    return json.dumps(expected, ensure_ascii=False, sort_keys=True)


def to_messages(role: str, sample: Mapping, system_prompt: str, *, with_answer: bool = True) -> list[dict]:
    """샘플 -> 서버 형식의 messages. with_answer=False면 추론 입력(system, user)만 낸다."""
    msgs = [{"role": "system", "content": system_prompt}, {"role": "user", "content": render_input(role, sample["input"])}]
    if with_answer:
        msgs.append({"role": "assistant", "content": render_output(role, sample["expected"])})
    return msgs


def sample_id_of(scenario_id: str, user_text: str, assistant_text: str) -> str:
    h = canonical.sha256_hash([user_text, assistant_text])[len("sha256:"):][:12]
    return f"{scenario_id}-{h}"


def from_server_row(role: str, row: Mapping, *, where: str = "row") -> tuple[dict, str]:
    """서버 줄 하나 -> (샘플, 시스템 프롬프트). 계약 스키마(ActionExtractionSample/ResponseSample)로 검증한다."""
    _check_role(role)
    try:
        if schemas.errors("ServerDatasetRow", row):      # messages가 system, user, assistant 순서인지 등 줄 형식은 스키마가 정한다
            raise ConversionError(f"{where}: 서버 줄 형식 위반(ServerDatasetRow)")
        msgs = row["messages"]
        system, user, assistant = (m["content"] for m in msgs)
        inp = json.loads(user)
        expected = json.loads(assistant) if role == "LORA_A" else assistant
        split = SERVER_SPLIT[row["split"]]
        sample = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION,
                  "sample_id": sample_id_of(row["scenario_id"], user, assistant),
                  "scenario_id": row["scenario_id"], "split": split, "origin": "SYNTHETIC",
                  "input": inp, "expected": expected}
        if "human_review" in row:
            sample["human_review"] = row["human_review"]
    except ConversionError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise ConversionError(f"{where}: 서버 줄 형식 위반({type(e).__name__})") from None
    try:
        validator(SAMPLE_CONTRACT_V2[role]).validate(sample)
    except Exception as e:
        raise ConversionError(f"{where}: {SAMPLE_CONTRACT_V2[role]} 위반({getattr(e, 'validator', type(e).__name__)})") from None
    return sample, system


def to_server_row(role: str, sample: Mapping, system_prompt: str) -> dict:
    """샘플 -> 서버 줄(학습 서버의 train_lora_*.py가 읽는 형식). from_server_row의 역변환."""
    row = {"scenario_id": sample["scenario_id"], "split": SAMPLE_SPLIT[sample["split"]],
           "messages": to_messages(role, sample, system_prompt)}
    if "human_review" in sample:
        row["human_review"] = sample["human_review"]
    return row


def read_server_file(role: str, path: Path) -> tuple[list[dict], str]:
    """서버 JSONL 파일 -> (샘플 목록, 시스템 프롬프트). 시스템 프롬프트가 한 종류가 아니면 중단한다."""
    samples: list[dict] = []
    prompts: set = set()
    with open(path, "r", encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                raise ConversionError(f"{Path(path).name}:{n} JSON이 아니다") from None
            s, sp = from_server_row(role, row, where=f"{Path(path).name}:{n}")
            samples.append(s)
            prompts.add(sp)
    if len(prompts) != 1:
        raise ConversionError(f"{Path(path).name}: 시스템 프롬프트가 {len(prompts)}종이다(한 종이어야 한다)")
    return samples, next(iter(prompts))


def write_samples(samples: Iterable[Mapping], path: Path) -> None:
    """샘플 JSONL(키 순서를 보존한다. state 순서가 입력 글자에 쓰이기 때문이다)."""
    with open(path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")


def to_chat_example(role: str, sample: Mapping, system_prompt: str) -> dict:
    """학습기에 넘기는 예시: 채팅 템플릿에 넣을 입력 messages와 정답 글자. (채팅 템플릿 적용은 학습기 몫)"""
    return {"sample_id": sample["sample_id"], "messages": to_messages(role, sample, system_prompt, with_answer=False),
            "completion": render_output(role, sample["expected"])}
