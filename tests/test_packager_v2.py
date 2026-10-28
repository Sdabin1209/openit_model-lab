"""2.0.0 패키징: 시스템 프롬프트(D75)가 어댑터 디렉터리 안에 들어가 adapter_hash에 포함되고 매니페스트가 가리킨다.
데이터셋 읽기(LORA_A/LORA_B 역할, ActionExtractionSample/ResponseSample)와 설정의 system_prompt_file도 함께 본다."""
import json
from pathlib import Path

import model_runtime
import pytest
import yaml
from boundary_contracts import PIPELINE_SCHEMA_VERSION, BoundaryError, canonical, validator

from tests.doubles.contract_facts import pipeline_version_pattern
from model_lab import datasets, packager
from model_lab.config import ConfigError, ROLE_TO_FILE_ROLE, load_config, system_prompt_text
from model_lab.registry import FileModelRegistry, MemoryModelRegistry
from model_lab.transform import read_server_file, write_samples

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).parent / "data"
H = "sha256:" + "a" * 64


def dataset_manifest():
    qs = {"id": "qs", "version": "1", "content_hash": H}
    return {"question_sets": [qs], "files": [
        {"path": "a-train.jsonl", "role": "LORA_A", "split": "TRAIN", "sample_count": 1, "content_hash": H},
        {"path": "a-eval.jsonl", "role": "LORA_A", "split": "EVAL", "sample_count": 1, "content_hash": H}]}


def adapter(tmp_path):
    ad = tmp_path / "adapter"
    ad.mkdir()
    (ad / "adapter_config.json").write_text('{"r": 16}')
    (ad / "adapter_model.safetensors").write_bytes(b"weights")
    return ad


def manifest(ad, sp, role="LORA_A"):
    sc = model_runtime.fake_serving_config()
    return packager.build_manifest(
        role=role, package_id="lora-a-x", version="v1", created_at="2026-10-27T00:00:00Z", contract_version=1,
        base_model={"id": "Qwen/Qwen3-4B", "revision": "r"}, adapter_dir=ad, serving_config=sc,
        dataset_ref={"id": "d", "version": "1", "content_hash": H}, dataset_manifest=dataset_manifest(), run_id="run-1",
        ext={}, system_prompt=sp)


def test_prompt_file_is_in_adapter_dir_and_manifest_points_at_it(tmp_path):
    ad = adapter(tmp_path)
    text = (ROOT / "prompts" / "lora_a.system.txt").read_text(encoding="utf-8")
    sp = packager.install_system_prompt(ad, text)
    assert sp == {"path": "system_prompt.txt", "sha256": canonical.file_hash(ad / "system_prompt.txt")}
    assert (ad / "system_prompt.txt").read_bytes() == text.encode("utf-8")              # 바이트 그대로(줄바꿈을 고치지 않는다)
    m = manifest(ad, sp)
    validator("ModelPackageManifest").validate(m)
    assert m["system_prompt"] == sp and m["pipeline_schema_version"] == PIPELINE_SCHEMA_VERSION and pipeline_version_pattern().fullmatch(m["pipeline_schema_version"])
    assert m["adapter_hash"] == canonical.directory_hash(ad)                          # 프롬프트 파일이 어댑터 해시에 들어 있다


def test_changing_the_prompt_changes_adapter_hash_and_package_content_hash(tmp_path):
    (tmp_path / "1").mkdir(); (tmp_path / "2").mkdir()
    ad1, ad2 = adapter(tmp_path / "1"), adapter(tmp_path / "2")
    m1 = manifest(ad1, packager.install_system_prompt(ad1, "프롬프트 하나"))
    m2 = manifest(ad2, packager.install_system_prompt(ad2, "프롬프트 둘"))
    assert m1["adapter_hash"] != m2["adapter_hash"] and m1["content_hash"] != m2["content_hash"]


def test_manifest_cannot_be_built_with_a_prompt_that_is_not_in_the_adapter(tmp_path):
    ad = adapter(tmp_path)
    sp = packager.install_system_prompt(ad, "p")
    (ad / "system_prompt.txt").write_bytes(b"changed after hashing")
    with pytest.raises(BoundaryError) as e:
        manifest(ad, sp)
    assert e.value.code == "INTEGRITY_ERROR"
    with pytest.raises(BoundaryError):
        manifest(ad, {"path": "none.txt", "sha256": H})


@pytest.mark.parametrize("kind", ["memory", "file"])
def test_package_with_prompt_publishes_and_comes_back_with_the_prompt(tmp_path, kind):
    ad = adapter(tmp_path)
    m = manifest(ad, packager.install_system_prompt(ad, "한국어 시스템 프롬프트"))
    reg = MemoryModelRegistry() if kind == "memory" else FileModelRegistry(tmp_path / "reg")
    assert reg.publish(m, ad, b"report") == m
    out = reg.open_adapter("lora-a-x", "v1", dest=tmp_path / "out")
    assert (out / m["system_prompt"]["path"]).read_text(encoding="utf-8") == "한국어 시스템 프롬프트"
    assert canonical.file_hash(out / m["system_prompt"]["path"]) == m["system_prompt"]["sha256"]


def test_repo_prompts_are_the_ones_in_the_server_dataset_rows():
    """prompts/*.txt는 학습 서버 데이터의 system 메시지와 같아야 한다(어긋나면 학습과 서빙의 프롬프트가 달라진다)."""
    for role, f, name in (("LORA_A", "server_rows_lora_a.jsonl", "lora_a"), ("LORA_B", "server_rows_lora_b.jsonl", "lora_b")):
        _, sysp = read_server_file(role, DATA / f)
        assert (ROOT / "prompts" / f"{name}.system.txt").read_bytes() == sysp.encode("utf-8")


def test_config_requires_system_prompt_file_and_reads_it(tmp_path):
    raw = yaml.safe_load((ROOT / "configs" / "dryrun.yaml").read_text())
    raw["profile"] = str((ROOT / "configs" / raw["profile"]).resolve()) if raw.get("profile") else None
    raw = {k: v for k, v in raw.items() if v is not None}
    raw["inputs"]["schema_dir"] = str(tmp_path)
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(raw))
    cfg = load_config(p)
    assert system_prompt_text(dict(cfg, _base_dir=str(ROOT / "configs")), "LORA_B") == (ROOT / "prompts" / "lora_b.system.txt").read_text(encoding="utf-8")
    del raw["roles"]["LORA_A"]["system_prompt_file"]
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match="system_prompt_file"):
        load_config(p)
    raw["roles"]["LORA_A"]["system_prompt_file"] = "nope.txt"
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match="읽을 수 없다"):
        system_prompt_text(load_config(p), "LORA_A")


def test_dataset_files_use_lora_roles_and_v2_sample_schemas(tmp_path):
    assert ROLE_TO_FILE_ROLE == {"LORA_A": "LORA_A", "LORA_B": "LORA_B"}
    for role, f in (("LORA_A", "server_rows_lora_a.jsonl"), ("LORA_B", "server_rows_lora_b.jsonl")):
        samples, _ = read_server_file(role, DATA / f)
        p = tmp_path / f"{role}.jsonl"
        write_samples(samples, p)
        assert list(datasets.read_samples(p, role)) == samples                           # schema_dir 없이 읽는다
    bad = json.loads((tmp_path / "LORA_A.jsonl").read_text(encoding="utf-8").splitlines()[0])
    bad["expected"] = {"action": "NEXT"}
    (tmp_path / "bad.jsonl").write_text(json.dumps(bad, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(BoundaryError) as e:
        list(datasets.read_samples(tmp_path / "bad.jsonl", "LORA_A"))
    assert e.value.code == "INVALID_RECORD" and bad["input"]["user_utterance"] not in str(e.value)
