"""시험용 v2 데이터셋(ActionExtractionSample/ResponseSample + DatasetManifest + JSONL 파일)을 결정적으로 만든다.

2.0.0(D74): v1 golden(요구 작성자의 정적 예시)은 v2 형식이 아니라 더 쓰지 않는다. 여기서는 학습 서버 데이터(dataset_10_14)와
같은 모양의 **합성** 샘플을 템플릿으로 만든다. 문항·문장은 시험용이며 품질 판단에 쓰지 않는다(서버 데이터를 복사한 것이 아니다).
분할은 시나리오 ID 해시로 정한다(시나리오 단위로 분리). 문항 세트: 학습·검증은 qs-train, 평가의 일부는 qs-unseen(보지 못한 문항 세트 시험).
LoRA-A 시나리오마다 LoRA-B 샘플이 하나 이상 대응한다(서버 데이터처럼 같은 시나리오를 공유한다)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from boundary_contracts import PIPELINE_SCHEMA_VERSION, canonical

ROLES = ("LORA_A", "LORA_B")
ROLE_FILE = {"LORA_A": "lora_a", "LORA_B": "lora_b"}
TOPIC = {1: "뇌졸중 진단과 복용", 2: "심근경색·협심증 진단과 복용", 3: "고혈압 진단과 복용", 4: "당뇨병 진단과 복용"}
ASK_TEXT = {1: "뇌졸중으로 진단받았거나 약을 드시나요?", 2: "심근경색이나 협심증으로 진단받았거나 약을 드시나요?",
            3: "고혈압으로 진단받았거나 약을 드시나요?", 4: "당뇨병으로 진단받았거나 약을 드시나요?"}
SYSTEM_PROMPT = {"LORA_A": "(시험용) 환자의 말 1턴을 읽고 저장할 값과 다음 절차를 JSON으로 출력하세요.",
                 "LORA_B": "(시험용) 정해진 절차와 값·다음 질문을 환자에게 존댓말 1~3문장으로 전하세요. 응답 문장만 출력하세요."}


def qs_ref(name: str, version: str = "1") -> dict:
    return {"id": name, "version": version, "content_hash": "sha256:" + hashlib.sha256(f"{name}@{version}".encode()).hexdigest()}


def qid(n: int) -> str:
    return f"Q1.D{n:02d}"


def nxt(n: int) -> str:
    return qid(n + 1) if n < 4 else "END"


def _cell(n: int, field: str, value, status="ANSWERED", precision="exact", save=True) -> dict:
    return {"key": f"{qid(n)}.{field}", "value": value, "precision": precision, "status": status, "save": save}


def _missing(n: int, field: str) -> dict:
    return _cell(n, field, None, "MISSING", "unspecified", False)


def _yn(v: bool) -> str:
    return "있음" if v else "없음"


def _a_b_pair(kind: str, n: int, k: int):
    """(A 입력, A 정답, B 입력 목록, B 정답 문장 목록). kind마다 한 상황. n=문항 번호(1~4), k=변형 번호."""
    diag, med = bool(k % 2), bool((k // 2) % 2)
    base_in = {"question_id": qid(n), "current_question": ASK_TEXT[n], "state": {}}
    topic = TOPIC[n]
    nq = nxt(n)
    next_b = ({"type": "NEXT", "question_id": nq, "text": ASK_TEXT[n + 1]} if nq != "END" else {"type": "END"})
    same_b = {"type": "SAME", "question_id": qid(n), "text": ASK_TEXT[n]}
    if kind == "NEXT":
        utt = f"진단은 {'받았고' if diag else '받은 적 없고'} 약은 {'먹어요' if med else '안 먹어요'} ({k})"
        a_out = {"action": "NEXT", "next_item": nq, "values": [_cell(n, "diagnosed", diag), _cell(n, "on_medication", med)]}
        known = f"진단 {_yn(diag)}, 약 {_yn(med)}"
        b_in = {"action": "NEXT", "topic": topic, "user_utterance": utt, "known": known, "next": next_b}
        sent = f"{topic}은 {known}으로 알겠습니다. " + (ASK_TEXT[n + 1] if nq != "END" else "문진을 마칩니다. 수고하셨습니다.")
    elif kind == "CORRECT":
        utt = f"아까 말씀드린 건 잘못이고 약은 {'먹어요' if med else '안 먹어요'} ({k})"
        base_in["state"] = {f"{qid(n)}.diagnosed": {"value": diag, "status": "ANSWERED"},
                            f"{qid(n)}.on_medication": {"value": not med, "status": "ANSWERED"}}
        a_out = {"action": "CORRECT", "next_item": nq, "values": [_cell(n, "on_medication", med, "CORRECTION")]}
        known = f"약 {_yn(med)}"
        b_in = {"action": "CORRECT", "topic": topic, "user_utterance": utt, "known": known, "next": next_b}
        sent = f"약은 {_yn(med)}으로 고쳤습니다. " + (ASK_TEXT[n + 1] if nq != "END" else "문진을 마칩니다.")
    elif kind == "REASK":
        utt = f"진단은 {'받았어요' if diag else '안 받았어요'} ({k})"
        a_out = {"action": "REASK", "next_item": qid(n), "reason": "MISSING", "targets": [f"{qid(n)}.on_medication"],
                 "values": [_cell(n, "diagnosed", diag), _missing(n, "on_medication")]}
        known = f"진단 {_yn(diag)}"
        b_in = {"action": "REASK", "topic": topic, "user_utterance": utt, "known": known, "reason": "MISSING",
                "ask": "지금 약을 드시는지", "next": same_b}
        sent = f"진단은 {_yn(diag)}으로 알겠습니다. 지금 약을 드시는지도 알려 주시겠어요?"
    elif kind == "CONFIRM":
        utt = f"약은 {'먹어요' if med else '안 먹어요'} ({k})"
        base_in["state"] = {f"{qid(n)}.on_medication": {"value": not med, "status": "ANSWERED"}}
        a_out = {"action": "CONFIRM", "next_item": qid(n), "reason": "CONFLICT_WITH_PREVIOUS", "targets": [f"{qid(n)}.on_medication"],
                 "values": [_cell(n, "on_medication", med, "CONFLICT", "exact", False)]}
        b_in = {"action": "CONFIRM", "topic": topic, "user_utterance": utt, "known": "", "reason": "CONFLICT_WITH_PREVIOUS",
                "ask": "이번 값이 맞는지 아까 값이 맞는지", "previous": f"약 {_yn(not med)}", "current": f"약 {_yn(med)}", "next": same_b}
        sent = f"아까는 약 {_yn(not med)}이라고 하셨는데 이번에는 약 {_yn(med)}이라고 하셨어요. 어느 쪽이 맞을까요?"
    elif kind == "HOLD":
        utt = f"글쎄요 잘 모르겠어요 ({k})"
        a_out = {"action": "HOLD", "next_item": nq, "reason": "UNCERTAIN", "targets": [f"{qid(n)}.diagnosed"],
                 "values": [_cell(n, "diagnosed", None, "UNKNOWN", "unspecified", False)]}
        b_in = {"action": "HOLD", "topic": topic, "user_utterance": utt, "known": "", "reason": "UNCERTAIN", "hold": "진단 여부", "next": next_b}
        sent = "진단 여부는 비워 두고 넘어가겠습니다. " + (ASK_TEXT[n + 1] if nq != "END" else "문진을 마칩니다.")
    elif kind == "SKIP":
        utt = f"그건 말하기 싫어요 ({k})"
        a_out = {"action": "SKIP", "next_item": nq, "reason": "REFUSED", "targets": [f"{qid(n)}.diagnosed"],
                 "values": [_cell(n, "diagnosed", None, "REJECTED", "unspecified", False)]}
        b_in = {"action": "SKIP", "topic": topic, "user_utterance": utt, "known": "", "reason": "REFUSED", "hold": "진단 여부", "next": next_b}
        sent = "말씀하기 어려우신 부분은 건너뛸게요. " + (ASK_TEXT[n + 1] if nq != "END" else "문진을 마칩니다.")
    elif kind == "OFF_TOPIC":
        utt = f"점심에 뭘 먹을지 모르겠어요 ({k})"
        a_out = {"action": "REDIRECT", "next_item": qid(n), "reason": "OFF_TOPIC", "values": []}
        b_in = {"action": "REDIRECT", "topic": topic, "user_utterance": utt, "known": "", "reason": "OFF_TOPIC", "next": same_b}
        sent = f"그렇군요. 문진을 이어 갈게요. {ASK_TEXT[n]}"
    else:   # OUT_OF_SCHEMA
        utt = f"요즘 허리가 자주 아파요 ({k})"
        a_out = {"action": "REDIRECT", "next_item": qid(n), "reason": "OUT_OF_SCHEMA", "note": "허리 통증", "values": []}
        b_in = {"action": "REDIRECT", "topic": topic, "user_utterance": utt, "known": "", "reason": "OUT_OF_SCHEMA", "note": "허리 통증",
                "next": same_b}
        sent = f"허리가 아프시다는 말씀은 담당자에게 전해 둘게요. {ASK_TEXT[n]}"
    a_in = dict(base_in, user_utterance=utt)
    return a_in, a_out, b_in, sent


KINDS = ("NEXT", "NEXT", "CORRECT", "REASK", "CONFIRM", "HOLD", "SKIP", "OFF_TOPIC", "OUT_OF_SCHEMA")


def scenarios(count: int = 180) -> list[dict]:
    """[{scenario_id, a_in, a_out, b_in, b_out, extra_b(START 시나리오 여부)}]"""
    out = []
    for i in range(count):
        kind, n, k = KINDS[i % len(KINDS)], 1 + (i // len(KINDS)) % 4, i // 36
        a_in, a_out, b_in, sent = _a_b_pair(kind, n, i)
        out.append({"scenario_id": f"fx_{kind.lower()}_q{n}_{i:04d}", "a_in": a_in, "a_out": a_out, "b_in": b_in, "b_out": sent})
    return out


def split_of(scenario_id: str) -> str:
    h = int(hashlib.sha256(scenario_id.encode()).hexdigest(), 16) % 10
    return "EVAL" if h < 2 else ("VALIDATION" if h < 3 else "TRAIN")


def start_scenario() -> dict:
    return {"scenario_id": "fx_start_0000", "b_in": {"action": "START", "topic": TOPIC[1], "next": {"type": "NEXT", "question_id": "Q1.D01", "text": ASK_TEXT[1]}},
            "b_out": f"안녕하세요. 문진표를 함께 작성해 볼게요. {ASK_TEXT[1]}"}


def load_samples(role: str, limit_scenarios: int | None = None) -> list[dict]:
    """계약 샘플(question_set 없음, 서버 데이터처럼)을 낸다. split은 시나리오 해시에서 정한다."""
    out = []
    scs = scenarios(limit_scenarios or 180)
    for sc in scs:
        split = split_of(sc["scenario_id"])
        s = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "sample_id": f"{sc['scenario_id']}-{role[-1].lower()}".lower(),
             "scenario_id": sc["scenario_id"], "split": split, "origin": "SYNTHETIC", "human_review": "auto_only",
             "input": sc["a_in"] if role == "LORA_A" else sc["b_in"], "expected": sc["a_out"] if role == "LORA_A" else sc["b_out"]}
        out.append(s)
    if role == "LORA_B":
        st = start_scenario()
        out.append({"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "sample_id": "fx_start_0000-b", "scenario_id": st["scenario_id"],
                    "split": split_of(st["scenario_id"]), "origin": "SYNTHETIC", "human_review": "auto_only", "input": st["b_in"], "expected": st["b_out"]})
    return out


def build_dataset(dest: Path, dataset_id: str, version: str, *, max_cases: int | None = None, unseen_eval: bool = True) -> dict:
    """dest/<dataset_id>/<version>/ 에 파일과 manifest.json을 쓰고 매니페스트를 낸다.

    파일 단위로 문항 세트를 적는다(files[].question_sets). 보지 못한 문항 세트 시험을 위해 평가 파일을 둘로 나눈다:
    시나리오 해시가 홀수면 qs-unseen 파일, 아니면 qs-train 파일. 샘플에는 question_set을 적지 않는다(서버 데이터처럼)."""
    root = dest / dataset_id / version
    root.mkdir(parents=True, exist_ok=True)
    qs_train, qs_un = qs_ref("qs-train"), qs_ref("qs-unseen")
    files = []
    for role in ROLES:
        groups: dict = {}
        for s in load_samples(role, max_cases):
            qs = qs_un if (s["split"] == "EVAL" and unseen_eval and int(hashlib.sha256(s["scenario_id"].encode()).hexdigest(), 16) % 2) else qs_train
            groups.setdefault((s["split"], qs["id"]), (qs, []))[1].append(s)
        for (split, _), (qs, ss) in sorted(groups.items()):
            name = f"{ROLE_FILE[role]}-{split.lower()}-{qs['id']}.jsonl"
            with open(root / name, "w", encoding="utf-8") as fh:
                for s in ss:
                    fh.write(json.dumps(s, ensure_ascii=False) + "\n")
            files.append({"path": name, "role": role, "split": split, "sample_count": len(ss),
                          "content_hash": canonical.file_hash(root / name), "question_sets": [qs]})
    m = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "dataset_id": dataset_id, "version": version,
         "created_at": "2026-10-27T00:00:00Z", "runtime_contract_version": 1, "question_sets": [qs_train, qs_un],
         "generator_version": "fixture-v2-1", "seed_case_ids": [], "turn_engine_version": "0.3.0",
         "scenario_disjoint_across_splits": True, "files": files}
    m["content_hash"] = canonical.content_hash(m)
    (root / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    vp = dest / dataset_id / "versions.json"
    vs = json.loads(vp.read_text()) if vp.exists() else []
    if version not in vs:
        vs.append(version)
    vp.write_text(json.dumps(vs))
    return m
