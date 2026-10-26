"""평가기(v2)가 모델 없이 도는지, 채점 규칙이 학습 서버 score_a_v2.py와 같은지, B 문장 규칙이 맞게 세는지 확인한다.
재료는 tests/doubles/fixtures.py의 합성 샘플이다(오라클 모델 = 정답을 그대로 내는 가짜)."""
import copy
import json
from pathlib import Path

import model_runtime
import pytest

from model_lab.backends.fake import oracle_backend
from model_lab.evaluate import build_report, same, score_a
from model_lab.text_rules import check_response, numbers
from model_lab.transform import render_output
from tests.conftest import SCHEMA_DIR
from tests.doubles.fixtures import SYSTEM_PROMPT, load_samples

QS = {"id": "qs", "version": "1", "content_hash": "sha256:" + "0" * 64}


def serving():
    c = model_runtime.fake_serving_config()
    c["decoding"]["constraint_mode"] = "none"
    c["constraint"]["supported_features"] = []      # oracle_backend의 기본(제약 기능 없음)과 맞춘다
    return c


def session_for(samples_by_role, tmp_path, **kw):
    sc = serving()
    mp = {}
    for role, ss in samples_by_role.items():
        for s in ss:
            mp[model_runtime.render_prompt(role, s["input"], serving_config=sc, system_prompt=SYSTEM_PROMPT[role], schema_dir=SCHEMA_DIR)] = render_output(role, s["expected"])
    ad, prompts = {}, {}
    from boundary_contracts import canonical
    for role in ("LORA_A", "LORA_B"):
        d = tmp_path / role; d.mkdir(exist_ok=True)
        (d / "sp.txt").write_bytes(SYSTEM_PROMPT[role].encode())
        ad[role], prompts[role] = d, {"path": "sp.txt", "sha256": canonical.file_hash(d / "sp.txt")}
    cfgs = {"LORA_A": sc, "LORA_B": sc}
    return model_runtime.load(ad, cfgs, schema_dir=SCHEMA_DIR, system_prompts=prompts,
                              expected_serving_config_hashes={r: model_runtime.serving_config_hash(sc) for r in cfgs}, backend=oracle_backend(mp, **kw))


def with_qs(ss):
    return [{**s, "question_set": QS} for s in ss]


def test_evaluator_runs_on_fixture_with_oracle_model(tmp_path):
    A, B = with_qs(load_samples("LORA_A")), with_qs(load_samples("LORA_B"))
    session = session_for({"LORA_A": A, "LORA_B": B}, tmp_path)
    for role, ss in (("LORA_A", A), ("LORA_B", B)):
        rep = build_report(role, session, ss, seen_sets={("qs", "1")}, eval_files=[], max_listed_failures=1000)
        assert rep["overall"]["n"] == len(ss) and rep["overall"]["raw_valid"] == len(ss) and rep["overall"]["prot_exact"] == len(ss)
        assert rep["failures"]["count"] == 0 and rep["overall"]["outcomes"] == {"ok": len(ss)}


def test_a_has_no_protection_invalid_output_is_a_failure_not_a_substitute(tmp_path):
    A = with_qs(load_samples("LORA_A"))
    session = session_for({"LORA_A": A, "LORA_B": []}, tmp_path, corrupt_every=3)
    rep = build_report("LORA_A", session, A, seen_sets={("qs", "1")}, eval_files=[])
    o = rep["overall"]
    assert o["raw_valid"] < o["n"] and o["outcomes"]["invalid"] == o["n"] - o["raw_valid"] and o["protections"] == {}
    assert o["counts"]["schema_fail"] == o["outcomes"]["invalid"]
    assert o["counts"]["gold_keys"] > o["counts"]["key_found"]              # 형식 실패 행의 정답 칸도 분모에 들어간다


def test_b_fallback_when_output_is_empty(tmp_path):
    B = with_qs(load_samples("LORA_B"))
    session = session_for({"LORA_A": [], "LORA_B": B}, tmp_path, corrupt_every=3)
    rep = build_report("LORA_B", session, B, seen_sets={("qs", "1")}, eval_files=[])
    o = rep["overall"]
    assert o["fallback"] > 0 and o["outcomes"]["fallback"] == o["fallback"] and o["protections"] == {"G0": o["fallback"], "G5": o["fallback"]}


GOLD = {"action": "CORRECT", "next_item": "Q2", "values": [
    {"key": "Q1.a", "value": 5, "precision": "exact", "status": "CORRECTION", "save": True},
    {"key": "Q1.b", "value": True, "precision": "exact", "status": "ANSWERED", "save": True}]}


def mutated(fn):
    p = copy.deepcopy(GOLD); fn(p); return p


def test_score_a_rules_match_score_a_v2():
    assert same(5, 5.0) and not same(True, 1) and not same(0, False) and same(None, None) and not same(10 ** 400, 1.0)
    full = score_a(GOLD, copy.deepcopy(GOLD))
    assert full["exact"] == 1 and full["cell"] == 2 and full["db_row"] == 1 and full["gold_keys"] == 2
    assert score_a(GOLD, mutated(lambda p: p["values"].reverse()))["exact"] == 1                  # values 순서 무시
    assert score_a(GOLD, mutated(lambda p: p["values"][0].update(value=5.0)))["exact"] == 1       # 5와 5.0
    c = score_a(GOLD, mutated(lambda p: p["values"][1].update(value=1)))                        # true와 1은 다름
    assert c["cell"] == 1 and c["value"] == 1 and c["exact"] == 0 and c["db_row"] == 0
    c = score_a(GOLD, mutated(lambda p: p["values"][0].update(precision="approximate")))        # precision만 달라도 칸 정답, 완전 일치만 깨짐
    assert c["cell"] == 2 and c["precision"] == 1 and c["exact"] == 0
    c = score_a(GOLD, mutated(lambda p: p["values"][0].update(save=False)))
    assert c["save"] == 1 and c["cell"] == 1 and c["db_row"] == 0
    c = score_a(GOLD, mutated(lambda p: p["values"].append(dict(p["values"][0], key="X.없는칸"))))
    assert c["extra_keys"] == 1 and c["exact"] == 0
    c = score_a(GOLD, mutated(lambda p: p["values"].pop()))
    assert c["key_found"] == 1 and c["exact"] == 0
    c = score_a(GOLD, mutated(lambda p: p.update(action="HOLD")))
    assert c["action"] == 0 and c["reason"] == 0 and c["next_item"] == 1
    assert score_a(GOLD, mutated(lambda p: p.update(targets=["Q1.a"])))["targets_extra_rows"] == 1
    # OOS: 정답이 REDIRECT인데 NEXT로 내면 FN, 형식 실패도 OOS 판정을 틀린 것으로 센다
    oos = {"action": "REDIRECT", "reason": "OFF_TOPIC", "next_item": "Q1", "values": []}
    assert score_a(oos, {**oos, "action": "NEXT"})["oos_fn"] == 1 and score_a(oos, None)["oos_fn"] == 1
    assert score_a(GOLD, {**GOLD, "action": "SKIP"})["oos_fp"] == 1 and score_a(GOLD, None)["oos_fp"] == 1
    assert score_a(GOLD, None)["schema_fail"] == 1 and score_a(GOLD, None)["gold_keys"] == 2


REQ = {"action": "NEXT", "topic": "t", "user_utterance": "삼십 분 걸어요", "known": "하루 30분",
       "next": {"type": "NEXT", "question_id": "Q1.D02", "text": "심근경색이나 협심증으로 진단받으셨나요?"}}


def test_text_rules_count_what_they_claim():
    ok = "하루 30분 걷는다고 알겠습니다. 심근경색이나 협심증으로 진단받으셨나요?"
    assert all(check_response(REQ, ok)[k] for k in ("ends_ok", "asks_next")) and not any(check_response(REQ, ok)[k] for k in ("new_number", "banned", "sentences_bad", "json_leak"))
    assert not check_response(REQ, "하루 30분이군요.")["ends_ok"] and not check_response(REQ, "알겠습니다. 다음 질문을 드릴게요?")["asks_next"]
    assert check_response(REQ, "하루 40분이군요. 다음은 있나요?")["new_number"]                  # 요청에 없는 숫자
    assert not check_response(REQ, "삼십 분이군요. 다음은 있나요?")["new_number"]                    # 한글 수사도 같은 수로 본다
    assert numbers("삼십 분, 이십삼 개, 열 개, 일주일") == {"30", "23", "10"}
    assert check_response(REQ, "꼭 병원에 가세요!")["banned"] and check_response(REQ, '{"a": 1}')["json_leak"]
    assert check_response(REQ, "하나. 둘. 셋. 넷. 다섯.")["sentences_bad"]
    end = {**REQ, "next": {"type": "END"}}
    assert check_response(end, "수고하셨습니다.")["ends_ok"] and not check_response(end, "더 있나요?")["ends_ok"]
