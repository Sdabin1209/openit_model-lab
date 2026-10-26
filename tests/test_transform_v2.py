"""학습 서버 messages 형식 <-> ActionExtractionSample/ResponseSample 변환(2.0.0, D74).
시험 재료 tests/data/server_rows_lora_*.jsonl은 서버 데이터셋(dataset_10_14)에서 고른 합성 줄이다(LoRA-A 7줄, LoRA-B 5줄)."""
import copy
import json
from pathlib import Path

import pytest
from boundary_contracts import validator

from model_lab.transform import (ConversionError, from_server_row, read_server_file, render_input, sample_id_of,
                                 to_chat_example, to_messages, to_server_row, write_samples)

DATA = Path(__file__).parent / "data"
FILES = {"LORA_A": DATA / "server_rows_lora_a.jsonl", "LORA_B": DATA / "server_rows_lora_b.jsonl"}


def rows(role):
    return [json.loads(l) for l in FILES[role].read_text(encoding="utf-8").splitlines() if l.strip()]


@pytest.mark.parametrize("role", ["LORA_A", "LORA_B"])
def test_every_row_becomes_a_valid_sample_and_goes_back_character_for_character(role):
    contract = {"LORA_A": "ActionExtractionSample", "LORA_B": "ResponseSample"}[role]
    for row in rows(role):
        s, sysp = from_server_row(role, row)
        validator(contract).validate(s)
        back = to_server_row(role, s, sysp)
        assert back["messages"] == row["messages"]                      # 글자 단위(서버 학습 코드가 보는 것과 같다)
        assert {k: back[k] for k in ("scenario_id", "split", "human_review")} == {k: row[k] for k in ("scenario_id", "split", "human_review")}


def test_fields_are_moved_not_invented():
    row = rows("LORA_A")[0]
    s, sysp = from_server_row("LORA_A", row)
    assert s["input"] == json.loads(row["messages"][1]["content"]) and s["expected"] == json.loads(row["messages"][2]["content"])
    assert s["pipeline_schema_version"] == "2.0" and s["origin"] == "SYNTHETIC" and s["split"] == "TRAIN"
    assert s["human_review"] == row["human_review"] and "question_set" not in s
    assert sysp == row["messages"][0]["content"] and sysp not in json.dumps(s, ensure_ascii=False)       # 프롬프트는 샘플에 없다


def test_lora_b_expected_is_the_sentence_itself_not_json():
    for row in rows("LORA_B"):
        s, _ = from_server_row("LORA_B", row)
        assert s["expected"] == row["messages"][2]["content"] and isinstance(s["expected"], str)
        assert "response_text" not in s["expected"][:20]                 # v1의 {"response_text": ...} 형태가 아니다


def test_splits_map_train_val_test_to_contract_names():
    splits = [from_server_row("LORA_A", r)[0]["split"] for r in rows("LORA_A")]
    assert splits[0] == "TRAIN" and splits[-2:] == ["VALIDATION", "EVAL"]


def test_state_order_is_kept_because_it_is_part_of_the_training_text():
    """서버 데이터의 state 키는 정렬되어 있지 않은 줄이 있다(5,077줄 중 1,014줄). 정렬하면 글자가 달라진다."""
    odd = [r for r in rows("LORA_A") if list(json.loads(r["messages"][1]["content"])["state"]) != sorted(json.loads(r["messages"][1]["content"])["state"])]
    assert odd, "시험 재료에 정렬되지 않은 state가 있어야 한다"
    s, _ = from_server_row("LORA_A", odd[0])
    assert to_messages("LORA_A", s, "p")[1]["content"] == odd[0]["messages"][1]["content"]
    resorted = copy.deepcopy(s["input"]); resorted["state"] = dict(sorted(resorted["state"].items()))
    assert render_input("LORA_A", resorted) != odd[0]["messages"][1]["content"]


def test_samples_file_keeps_state_order(tmp_path):
    odd = next(r for r in rows("LORA_A") if list(json.loads(r["messages"][1]["content"])["state"]) != sorted(json.loads(r["messages"][1]["content"])["state"]))
    s, sysp = from_server_row("LORA_A", odd)
    p = tmp_path / "s.jsonl"; write_samples([s], p)
    back = json.loads(p.read_text(encoding="utf-8"))
    assert to_server_row("LORA_A", back, sysp)["messages"] == odd["messages"]


def test_sample_ids_are_unique_stable_and_valid_even_when_scenario_id_repeats():
    b = rows("LORA_B")
    assert b[0]["scenario_id"] == b[1]["scenario_id"]                    # 서버 LoRA-B에는 같은 scenario_id가 여러 줄 있다
    ids = [from_server_row("LORA_B", r)[0]["sample_id"] for r in b]
    assert len(set(ids)) == len(ids)
    assert ids == [from_server_row("LORA_B", r)[0]["sample_id"] for r in b]
    assert all(len(i) <= 128 for i in ids)
    assert sample_id_of("s", "u", "a") != sample_id_of("s", "u", "b")


def test_chat_example_has_no_answer_in_the_prompt():
    row = rows("LORA_A")[0]
    s, sysp = from_server_row("LORA_A", row)
    ex = to_chat_example("LORA_A", s, sysp)
    assert [m["role"] for m in ex["messages"]] == ["system", "user"] and ex["completion"] == row["messages"][2]["content"]
    assert ex["messages"] == row["messages"][:2]


@pytest.mark.parametrize("mutate,why", [
    (lambda r: r["messages"].pop(0), "messages 순서"),
    (lambda r: r["messages"][1].update(content="{not json"), "JSON 아님"),
    (lambda r: r.update(split="dev"), "알 수 없는 split"),
    (lambda r: r["messages"][2].update(content=json.dumps({"action": "NEXT"})), "ActionExtraction 위반"),
])
def test_bad_rows_are_rejected_without_leaking_utterance(mutate, why):
    row = copy.deepcopy(rows("LORA_A")[0])
    utterance = json.loads(row["messages"][1]["content"])["user_utterance"]
    mutate(row)
    with pytest.raises(ConversionError) as e:
        from_server_row("LORA_A", row, where="f:1")
    assert utterance not in str(e.value) and str(e.value).startswith("f:1")


def test_unknown_role_and_wrong_answer_type_are_rejected():
    with pytest.raises(ConversionError):
        from_server_row("PARSER", rows("LORA_A")[0])
    s, sysp = from_server_row("LORA_B", rows("LORA_B")[0])
    s["expected"] = {"response_text": "x"}                              # v1 형태
    with pytest.raises(ConversionError):
        to_messages("LORA_B", s, sysp)


def test_read_server_file_requires_exactly_one_system_prompt(tmp_path):
    samples, sysp = read_server_file("LORA_A", FILES["LORA_A"])
    assert len(samples) == 7 and sysp
    p = tmp_path / "mixed.jsonl"
    rs = rows("LORA_A")[:2]
    rs[1]["messages"][0]["content"] = "다른 프롬프트"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rs), encoding="utf-8")
    with pytest.raises(ConversionError, match="시스템 프롬프트"):
        read_server_file("LORA_A", p)
