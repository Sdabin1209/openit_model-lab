"""정답 예시(golden)로 평가기가 모델 없이 도는지 확인한다.

golden은 요구 작성자의 정적 예시(correctness_claim=false)다. 차이는 (a) 구현 결함 (b) 해석 차이 (c) 예시 오류로 분류한다.
이 시험이 확인하는 차이의 분류는 docs/change-requests/CR-L001-golden-observations.md에 있다."""
import collections
import json
from pathlib import Path

import model_runtime
import pytest

from model_lab.backends.fake import oracle_backend
from model_lab.evaluate import build_report, evaluate_sample
from model_lab.schemas import survey_validator
from model_lab.transform import completion_of
from tests.conftest import SCHEMA_DIR
from tests.doubles.fixtures import load_pairs

PAIRS = load_pairs()
ROLE = {"PARSER": "LORA_A", "GENERATOR": "LORA_B"}


def serving():
    c = model_runtime.fake_serving_config()
    c["decoding"]["constraint_mode"] = "none"
    c["constraint"]["supported_features"] = []      # oracle_backend의 기본(제약 기능 없음)과 맞춘다
    return c


def test_golden_is_complete_and_matches_survey_schema():
    assert len(PAIRS) == 438
    vin = {"PARSER": "UtteranceParserInput", "GENERATOR": "ResponseGeneratorInput"}
    vout = {"PARSER": "ParserOutput", "GENERATOR": "GeneratorOutput"}
    for p in PAIRS:
        assert not list(survey_validator(SCHEMA_DIR, vin[p["role"]]).iter_errors(p["input"])), (p["case"], "input")
        assert not list(survey_validator(SCHEMA_DIR, vout[p["role"]]).iter_errors(p["expected"])), (p["case"], "output")


def _session_for(pairs, **kw):
    sc = serving()
    mp = {}
    for p in pairs:
        r = ROLE[p["role"]]
        mp[model_runtime.render_prompt(r, p["input"], serving_config=sc, schema_dir=SCHEMA_DIR)] = completion_of(r, p["expected"])
    be = oracle_backend(mp, **kw)
    cfgs = {"LORA_A": sc, "LORA_B": sc}
    ad = {"LORA_A": Path("/tmp"), "LORA_B": Path("/tmp")}
    return model_runtime.load(ad, cfgs, schema_dir=SCHEMA_DIR,
                              expected_serving_config_hashes={r: model_runtime.serving_config_hash(sc) for r in cfgs}, backend=be)


def sample_of(p):
    return {"sample_id": f"{p['case']}-{p['turn']}", "question_set": {"id": "qs", "version": "1", "content_hash": "sha256:" + "0" * 64},
            "input": p["input"], "expected": p["expected"]}


def test_evaluator_runs_on_golden_with_oracle_model():
    """평가기가 golden을 입력으로 끝까지 돈다. 모델은 정답을 그대로 내는 가짜다."""
    session = _session_for(PAIRS)
    diffs = collections.defaultdict(list)
    for role in ("PARSER", "GENERATOR"):
        ps = [p for p in PAIRS if p["role"] == role]
        rep = build_report(ROLE[role], session, [sample_of(p) for p in ps], seen_sets={("qs", "1")}, eval_files=[], max_listed_failures=1000)
        assert rep["overall"]["n"] == len(ps) and rep["overall"]["raw_valid"] == len(ps)     # 형식은 모두 유효
        for f in rep["failures"]["listed"]:
            diffs[role].append(f["sample_id"])
    # 모델이 정답을 내는데도 어긋나는 샘플 = 같은 입력에 다른 정답이 있는 사례(분류 (c), CR-L001). 구현 결함이 아니다.
    known = {"J1-1", "J2-1", "J7a-1", "J7b-1", "J7c-1", "J8c-1", "J8d-1", "J8e-1", "J8f-1", "J8h-1", "N14-1", "R15-1",
             "K9d-1", "R16-1", "S4b-1", "S4c-1"}
    assert set(diffs["PARSER"]) <= known and len(diffs["PARSER"]) == 12     # 충돌 그룹마다 하나만 이길 수 있다: 16 - 4 = 12
    assert diffs["GENERATOR"] == []


def test_golden_is_fallback_is_always_true_in_examples():
    """분류 (c) 예시 오류: 모든 생성기 예시의 is_fallback이 true다. 서빙은 정상 출력에 false를 낸다(D36). CR-L001."""
    gen = [p for p in PAIRS if p["role"] == "GENERATOR"]
    assert {p["expected"]["is_fallback"] for p in gen} == {True}
    session = _session_for(PAIRS)
    rep = build_report("LORA_B", session, [sample_of(p) for p in gen], seen_sets={("qs", "1")}, eval_files=[])
    assert rep["overall"]["fallback"] == 0 and "fallback_matches_expected" not in rep["overall"]    # 비교 대상이 아니다


def test_protection_path_on_golden_inputs():
    session = _session_for(PAIRS, corrupt_every=3)
    ps = [p for p in PAIRS if p["role"] == "PARSER"]
    rep = build_report("LORA_A", session, [sample_of(p) for p in ps], seen_sets={("qs", "1")}, eval_files=[])
    o = rep["overall"]
    assert o["raw_valid"] < o["n"] and o["outcomes"].get("fallback", 0) > 0
