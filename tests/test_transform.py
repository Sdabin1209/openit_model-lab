import json

import model_runtime

from model_lab.transform import completion_of, to_example
from tests.conftest import SCHEMA_DIR
from tests.doubles.fixtures import load_pairs


def sc():
    c = model_runtime.fake_serving_config(); c["decoding"]["constraint_mode"] = "none"; return c


def test_prompt_is_render_prompt_and_generator_target_is_response_text_only():
    pairs = load_pairs(5)
    g = next(p for p in pairs if p["role"] == "GENERATOR")
    ex = to_example("LORA_B", {"sample_id": "x", "input": g["input"], "expected": g["expected"]}, sc(), SCHEMA_DIR)
    assert ex["prompt"] == model_runtime.render_prompt("LORA_B", g["input"], serving_config=sc(), schema_dir=SCHEMA_DIR)
    assert json.loads(ex["completion"]) == {"response_text": g["expected"]["response_text"]}      # is_fallback은 코드가 채운다(D36)


def test_parser_target_is_full_parser_output_and_deterministic():
    p = next(p for p in load_pairs(5) if p["role"] == "PARSER")
    assert json.loads(completion_of("LORA_A", p["expected"])) == p["expected"]
    assert completion_of("LORA_A", p["expected"]) == completion_of("LORA_A", dict(reversed(list(p["expected"].items()))))
