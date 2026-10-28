"""단위 3: 손으로 검사하던 경계를 계약 스키마로 바꾼 곳(D107) + 이미지 라벨(ImageLabels). 기대값은 계약 스키마·예시에서 온다."""
import copy
import json
import re
from pathlib import Path

import pytest
import conformance
from boundary_contracts import BoundaryError, validator

from model_lab import regression
from model_lab.pipeline import Run
from tests.conftest import FIXED
from tests.doubles.fixtures import load_samples
from tests.test_registry import code, make, reg  # noqa: F401  (reg는 memory/file/http 공통 fixture)

CONF = Path(conformance.__file__).parent
ROOT = Path(__file__).resolve().parents[1]


def ex(kind: str, name: str):
    return json.loads((CONF / kind / name).read_text(encoding="utf-8"))


# ---- ModelPackageLayout: 어댑터 디렉터리는 필수 파일 3개뿐 ---------------------------------------------------------------------

def test_extra_file_in_adapter_dir_is_rejected_at_publish(reg, tmp_path):
    """examples_invalid/ModelPackageLayout.extra-file.json과 같은 경우: README.md가 어댑터 디렉터리에 있다. 계약은 모르는 파일을 거부한다."""
    assert any(k not in ("adapter_model.safetensors", "adapter_config.json", "system_prompt.txt")
               for k in ex("examples_invalid", "ModelPackageLayout.extra-file.json")["adapter_files"])    # 예시가 이 경우임을 확인
    m, ad, rep = make(tmp_path)
    (ad / "README.md").write_text("x")
    m["adapter_hash"] = __import__("boundary_contracts").canonical.directory_hash(ad)
    m["content_hash"] = __import__("boundary_contracts").canonical.content_hash(m)
    assert code(reg.publish, m, ad, rep) == "INVALID_RECORD"
    assert reg.list_ids()[0] == []                               # 흔적이 없다


def test_layout_of_matches_contract_example_shape(tmp_path):
    from model_lab.registry import layout_of
    m, ad, _ = make(tmp_path)
    lay = layout_of(ad, "LORA_A")
    assert set(lay) == set(ex("examples", "ModelPackageLayout.json")) and lay["adapter_hash"] == m["adapter_hash"]
    assert lay["system_prompt"] == m["system_prompt"]
    validator("ModelPackageLayout").validate(lay)


# ---- 회귀 평가 trace: OpsFeedbackDeidentifiedTrace ----------------------------------------------------------------------------

class _St:
    def __init__(self, trace, case): self.trace, self.case = trace, case
    def list_cases(self, **kw): return [self.case], None
    def get_trace(self, ref): return self.trace


def _case(kind, expected):
    return {"case_id": "c1", "version": 1, "expected_kind": kind, "expected": expected, "failure_type": "OTHER",
            "source_release_id": "r1", "deidentified_trace_ref": "t1", "review_status": "CONFIRMED"}


def _valid_trace():
    # 비식별 trace의 자유 문자열은 [MASKED]다(계약). 그래서 계약 예시를 그대로 쓴다.
    return ex("examples", "OpsFeedbackDeidentifiedTrace.json")


def test_valid_contract_trace_is_used():
    assert validator("OpsFeedbackDeidentifiedTrace").is_valid(_valid_trace())
    got = regression.build_set(_St(_valid_trace(), _case("LORA_A", load_samples("LORA_A")[0]["expected"])), None)
    assert len(got["samples"]["LORA_A"]) == 1 and got["skipped"] == {}


@pytest.mark.parametrize("name,mutate", [
    ("unknown-key", lambda t: t.update(extra_field=1)),
    ("missing-required", lambda t: t.pop("session_id")),
    ("original-session-id", lambda t: t.update(session_id="sess-1234")),
])
def test_trace_that_breaks_the_contract_is_skipped(name, mutate):
    """examples_invalid/OpsFeedbackDeidentifiedTrace.<name>.json과 같은 위반. 수정 전에는 extract_input만 있으면 그대로 평가에 들어갔다."""
    assert not validator("OpsFeedbackDeidentifiedTrace").is_valid(ex("examples_invalid", f"OpsFeedbackDeidentifiedTrace.{name}.json"))
    t = _valid_trace(); mutate(t)
    assert not validator("OpsFeedbackDeidentifiedTrace").is_valid(t)
    got = regression.build_set(_St(t, _case("LORA_A", load_samples("LORA_A")[0]["expected"])), None)
    assert got["samples"]["LORA_A"] == [] and got["skipped"]["trace_invalid"]["count"] == 1


# ---- turn_engine_version 표지(D109) ------------------------------------------------------------------------------------------

def test_turn_engine_mark_comes_from_config(cfg, tmp_path):
    from tests.test_pipeline import mk, with_dirs
    c = with_dirs(cfg, tmp_path, "mark"); c["lineage"] = {"turn_engine_version": "v9.8.7"}
    r = mk(c); rec = r.run()
    m = r.registry.get_manifest("lora-a-parser", rec["result"]["packages"]["LORA_A"]["version"])
    assert m["lineage"]["turn_engine_version"] == "v9.8.7"


def test_turn_engine_mark_defaults_to_v0_4_3(cfg, tmp_path):
    from tests.test_pipeline import mk, with_dirs
    r = mk(with_dirs(cfg, tmp_path, "default")); rec = r.run()
    m = r.registry.get_manifest("lora-a-parser", rec["result"]["packages"]["LORA_A"]["version"])
    assert m["lineage"]["turn_engine_version"] == "v0.4.3"


# ---- 이미지 라벨: 계약 ImageLabels ---------------------------------------------------------------------------------------------

def _dockerfile_labels(args: dict) -> dict:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    block = re.search(r"^LABEL (.*?)(?:\n(?!\s)|\Z)", text, re.S | re.M).group(1)
    labels = dict(re.findall(r'(io\.openit\.[\w.-]+)="([^"]*)"', block))
    return {k: re.sub(r"\$\{(\w+)\}", lambda m: args[m.group(1)], v) for k, v in labels.items()}


def test_dockerfile_labels_satisfy_image_labels_contract():
    labels = _dockerfile_labels({"REVISION": "3bfd0d7", "BC_VERSION": "v2.2.1"})
    assert len(labels) == 6
    validator("ImageLabels").validate(labels)


def test_dockerfile_build_args_have_no_placeholder_defaults():
    """라벨 값이 none/unknown/unset 같은 기본값으로 새면 추적이 거짓이 된다(examples_invalid/ImageLabels.revision-unknown.json 등)."""
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    for line in re.findall(r"^ARG .*$", text, re.M):
        assert "=" not in line, f"ARG 기본값이 있다: {line}"
    for bad in ("revision-unknown", "turn-engine-none", "model-runtime-unset"):
        assert not validator("ImageLabels").is_valid(ex("examples_invalid", f"ImageLabels.{bad}.json"))
