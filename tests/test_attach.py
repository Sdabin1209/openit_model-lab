"""어댑터 얹기: 어댑터 두 디렉터리 + 시스템 프롬프트 두 개 + 프로파일 -> 패키지 -> 게시 (`model-lab package-adapters`).
가짜 어댑터(작은 safetensors 파일)로 시험한다. 학습 서버에는 닿지 않는다."""
import json

import pytest
import yaml
from boundary_contracts import canonical, validator

from model_lab.attach import package_adapters
from model_lab.cli import main as cli_main
from model_lab.errors import ConfigError, PipelineError
from model_lab.registry import FileModelRegistry
from tests.conftest import ROOT, SCHEMA_DIR

H = "sha256:" + "b" * 64
DS = {"id": "openit-dataset", "version": "v1", "content_hash": H}
BASE = "Qwen/Qwen2.5-1.5B-Instruct"        # profiles/gpu-dev-12g.yaml의 기반 모델


def make_adapter(d, base=BASE, r=16, weights=b"fake-safetensors"):
    d.mkdir(parents=True)
    (d / "adapter_model.safetensors").write_bytes(weights)
    (d / "adapter_config.json").write_text(json.dumps({"base_model_name_or_path": base, "r": r, "peft_type": "LORA"}))
    (d / "README.md").write_text("not part of the package")
    return d


@pytest.fixture
def setup(tmp_path):
    raw = yaml.safe_load((ROOT / "configs" / "dryrun.yaml").read_text())
    for k in ("base_model", "training"):
        raw.pop(k)
    raw["inputs"]["schema_dir"] = str(SCHEMA_DIR)
    raw["inputs"]["dataset_store"]["args"]["root"] = str(tmp_path)
    for r in raw["roles"].values():
        r["system_prompt_file"] = str((ROOT / "configs" / r["system_prompt_file"]).resolve())
    raw["registry"] = {"kind": "file", "path": str(tmp_path / "registry")}
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(yaml.safe_dump(raw))
    pa, pb = tmp_path / "pa.txt", tmp_path / "pb.txt"
    pa.write_text("프롬프트 A\n"); pb.write_text("프롬프트 B\n")
    return dict(config=cfg, profile="gpu-dev-12g", adapters={"LORA_A": make_adapter(tmp_path / "a", weights=b"A"),
                                                              "LORA_B": make_adapter(tmp_path / "b", weights=b"B")},
                prompts={"LORA_A": pa, "LORA_B": pb}, out=tmp_path / "out", dataset=DS), tmp_path


def test_builds_valid_packages_publishes_and_is_idempotent(setup):
    kw, tmp = setup
    res = package_adapters(**kw)
    reg = FileModelRegistry(tmp / "registry")
    assert set(res) == {"LORA_A", "LORA_B"}
    for role, pid in (("LORA_A", "lora-a-parser"), ("LORA_B", "lora-b-generator")):
        v = res[role]["version"]
        m = reg.get_manifest(pid, v)
        validator("ModelPackageManifest").validate(m)
        assert m["role"] == role and m["base_model"]["id"] == BASE and m["system_prompt"]["path"] == "system_prompt.txt"
        assert m["ext"]["model-lab"]["evaluated"] is False
        dest = reg.open_adapter(pid, v, dest=tmp / f"open-{role}")
        assert (dest / "adapter_model.safetensors").is_file() and not (dest / "README.md").exists()
        assert canonical.directory_hash(dest) == m["adapter_hash"]
        rep = json.loads(reg.get_eval_report(m["eval_report_ref"]))
        validator("EvaluationReport").validate(rep)
        assert rep["metrics"][0]["n"] == 0 and "평가하지 않았다" in rep["notes"][0]      # 평가한 것처럼 쓰지 않는다
    assert package_adapters(**kw) == res                                                 # 같은 입력은 같은 패키지(멱등)


def test_rejects_adapter_trained_on_another_base_model(setup):
    kw, tmp = setup
    kw["adapters"]["LORA_B"] = make_adapter(tmp / "other", base="Qwen/Qwen3-4B")
    with pytest.raises(PipelineError, match="LORA_B.*기반 모델"):
        package_adapters(**kw)
    assert not (tmp / "registry").exists() or not list((tmp / "registry").glob("manifests/*"))   # 하나도 게시되지 않았다


def test_rejects_missing_file_rank_and_bad_profile(setup):
    kw, tmp = setup
    (kw["adapters"]["LORA_A"] / "adapter_model.safetensors").unlink()
    with pytest.raises(PipelineError, match="adapter_model.safetensors"):
        package_adapters(**kw)
    kw["adapters"]["LORA_A"] = make_adapter(tmp / "hi", r=64)
    with pytest.raises(PipelineError, match="max_lora_rank"):
        package_adapters(**kw)
    with pytest.raises(ConfigError):
        package_adapters(**{**kw, "profile": "../x"})
    with pytest.raises(ConfigError, match="프로파일이 없다"):
        package_adapters(**{**kw, "profile": "nope"})


def test_cli_no_publish_and_publish(setup, capsys):
    kw, tmp = setup
    argv = ["package-adapters", "--config", str(kw["config"]), "--profile", "gpu-dev-12g",
            "--adapter-a", str(kw["adapters"]["LORA_A"]), "--adapter-b", str(kw["adapters"]["LORA_B"]),
            "--prompt-a", str(kw["prompts"]["LORA_A"]), "--prompt-b", str(kw["prompts"]["LORA_B"]),
            "--dataset-id", DS["id"], "--dataset-version", DS["version"], "--dataset-hash", DS["content_hash"], "--out", str(kw["out"])]
    assert cli_main(argv + ["--no-publish"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["LORA_A"]["published"] is False and (kw["out"] / "package" / "LORA_A.manifest.json").is_file()
    assert cli_main(argv) == 0
    assert json.loads(capsys.readouterr().out)["LORA_B"]["published"] is True
    i = argv.index("--profile")
    assert cli_main(argv[:i] + argv[i + 2:]) == 2        # 설정에도 --profile에도 기반 모델이 없으면 오류
