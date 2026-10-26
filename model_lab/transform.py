"""학습 데이터 변환.

2.0.0(D74): 학습 서버의 messages 형식 <-> ActionExtractionSample/ResponseSample 변환기는 아래 "2.0.0" 절에 있다.
위쪽의 `completion_of`, `to_example`은 v1(model-runtime render_prompt, ParserOutput 기준)이며 폐기 예정이다.
파이프라인(pipeline.py)이 model-runtime 새 태그(v0.6.0 제안)로 옮겨 갈 때 `to_chat_example`로 바꾼다. 아직 바꾸지 않았다."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Mapping

from boundary_contracts import PIPELINE_SCHEMA_VERSION, canonical, validator

from .errors import PipelineError


def completion_of(role: str, expected: Mapping) -> str:
    """정답 출력 -> 모델이 내야 하는 문자열(결정적인 JCS JSON)."""
    if role == "LORA_B":
        obj = {"response_text": expected["response_text"]}
    else:
        obj = dict(expected)
    return canonical.canonicalize(obj).decode("utf-8")


def to_example(role: str, sample: Mapping, serving_config: Mapping, schema_dir: Path) -> dict:
    import model_runtime        # v1 경로에서만 쓴다(폐기 예정)
    prompt = model_runtime.render_prompt(role, sample["input"], serving_config=serving_config, schema_dir=schema_dir)
    return {"sample_id": sample["sample_id"], "prompt": prompt, "completion": completion_of(role, sample["expected"])}


# ---------------------------------------------------------------------------------------------------------------
# 2.0.0 (D74): 학습 서버 데이터셋의 messages 형식 <-> ActionExtractionSample / ResponseSample
#
# 학습 서버(`~/taehyeon/data/dataset_10_14`)의 한 줄은 {"scenario_id", "split", "human_review", "messages"}이고
# messages는 [system, user, assistant] 세 개다(확인: 10,264줄 전부). user는 입력 JSON 문자열, assistant는
# LoRA-A면 출력 JSON 문자열, LoRA-B면 응답 문장 그대로다. 서버 학습 코드(train_lora_a.py, train_lora_b.py)는 이 messages를
# 채팅 템플릿(enable_thinking=False)으로 바꿔 assistant 토큰에만 손실을 건다.
# 이 변환은 형식만 옮긴다. 값을 고치거나 채우지 않는다. 서버 줄에 없는 필드는 아래 규칙으로만 만든다.
#   sample_id : 서버 줄에 없다. {scenario_id}-{내용 해시 12자}. LoRA-B에는 같은 scenario_id가 여러 줄 있다(확인).
#   origin    : 서버 줄에 없다. 모두 SYNTHETIC으로 둔다(가정; FAILURE_CASE_SEED인지 알 수 없다).
#   split     : train -> TRAIN, val -> VALIDATION, test -> EVAL.
#   시스템 프롬프트 : 샘플에 담지 않는다. 역할마다 하나이며(확인) 패키지의 system_prompt 파일이 된다(D75).
# ---------------------------------------------------------------------------------------------------------------
SERVER_SPLIT = {"train": "TRAIN", "val": "VALIDATION", "test": "EVAL"}
SAMPLE_SPLIT = {v: k for k, v in SERVER_SPLIT.items()}
SAMPLE_CONTRACT_V2 = {"LORA_A": "ActionExtractionSample", "LORA_B": "ResponseSample"}
_ROLES = ("LORA_A", "LORA_B")


class ConversionError(PipelineError):
    """변환할 수 없는 줄. 발화 원문을 메시지에 담지 않는다(줄 번호와 이유만)."""


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


def to_chat_example(role: str, sample: Mapping, system_prompt: str) -> dict:
    """학습기에 넘기는 예시: 채팅 템플릿에 넣을 입력 messages와 정답 글자. (채팅 템플릿 적용은 학습기 몫)"""
    return {"sample_id": sample["sample_id"], "messages": to_messages(role, sample, system_prompt, with_answer=False),
            "completion": render_output(role, sample["expected"])}


def sample_id_of(scenario_id: str, user_text: str, assistant_text: str) -> str:
    h = canonical.sha256_hash([user_text, assistant_text])[len("sha256:"):][:12]
    return f"{scenario_id}-{h}"


def from_server_row(role: str, row: Mapping, *, where: str = "row") -> tuple[dict, str]:
    """서버 줄 하나 -> (샘플, 시스템 프롬프트). 계약 스키마(ActionExtractionSample/ResponseSample)로 검증한다."""
    _check_role(role)
    try:
        msgs = row["messages"]
        if [m["role"] for m in msgs] != ["system", "user", "assistant"]:
            raise ConversionError(f"{where}: messages는 system, user, assistant 순서여야 한다")
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
