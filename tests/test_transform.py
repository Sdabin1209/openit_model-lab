import json

import model_runtime

from model_lab.components.train_data import render_output, to_example
from tests.conftest import SCHEMA_DIR
from tests.doubles.fixtures import SYSTEM_PROMPT, load_samples


def sc():
    c = model_runtime.fake_serving_config(); c["decoding"]["constraint_mode"] = "none"; return c


def test_prompt_is_render_prompt_and_completion_is_the_training_server_text():
    for role in ("LORA_A", "LORA_B"):
        s = load_samples(role)[0]
        ex = to_example(role, s, sc(), SYSTEM_PROMPT[role], SCHEMA_DIR)
        assert ex["prompt"] == model_runtime.render_prompt(role, s["input"], serving_config=sc(), system_prompt=SYSTEM_PROMPT[role], schema_dir=SCHEMA_DIR)
        assert ex["completion"] == render_output(role, s["expected"]) and ex["sample_id"] == s["sample_id"]
    b = load_samples("LORA_B")[0]
    assert to_example("LORA_B", b, sc(), SYSTEM_PROMPT["LORA_B"], SCHEMA_DIR)["completion"] == b["expected"]     # B는 문장 그대로(JSON 아님)
    a = load_samples("LORA_A")[0]
    assert json.loads(to_example("LORA_A", a, sc(), SYSTEM_PROMPT["LORA_A"], SCHEMA_DIR)["completion"]) == a["expected"]
