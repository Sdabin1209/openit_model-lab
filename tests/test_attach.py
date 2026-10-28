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
        from boundary_contracts import metric_name_problems
        assert metric_name_problems(rep) == []                                           # 계약 레지스트리가 기대값(ML-01)
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


def test_qwen3_8b_profile_matches_runtime_profile_and_packages(setup):
    """프로파일 qwen3-8b: 기반 모델·rank 상한이 model-runtime rtx4090-24g와 같고, 8B 가짜 어댑터로 패키지가 만들어진다."""
    kw, tmp = setup
    rt = json.loads((ROOT.parent / "model-runtime" / "profiles" / "rtx4090-24g.json").read_text(encoding="utf-8")) \
        if (ROOT.parent / "model-runtime" / "profiles" / "rtx4090-24g.json").is_file() else None
    prof = yaml.safe_load((ROOT / "profiles" / "qwen3-8b.yaml").read_text(encoding="utf-8"))
    assert prof["base_model"]["id"] == "Qwen/Qwen3-8B" and prof["checks"]["max_lora_rank"] == 16
    if rt is not None:                                    # 이웃 리포가 없는 환경(이미지)에서는 건너뛴다
        assert rt["base_model"] == prof["base_model"]["id"] and rt["engine"]["max_lora_rank"] == prof["checks"]["max_lora_rank"]
    ok = {"LORA_A": make_adapter(tmp / "a8", base="Qwen/Qwen3-8B", weights=b"A8"),
          "LORA_B": make_adapter(tmp / "b8", base="Qwen/Qwen3-8B", weights=b"B8")}
    res = package_adapters(**{**kw, "profile": "qwen3-8b", "adapters": ok})
    reg = FileModelRegistry(tmp / "registry")
    for role, pid in (("LORA_A", "lora-a-parser"), ("LORA_B", "lora-b-generator")):
        m = reg.get_manifest(pid, res[role]["version"])
        validator("ModelPackageManifest").validate(m)
        assert m["base_model"]["id"] == "Qwen/Qwen3-8B"


def test_qwen3_8b_profile_rejects_4b_adapter_and_high_rank_without_publishing(setup):
    kw, tmp = setup
    good = make_adapter(tmp / "a8", base="Qwen/Qwen3-8B", weights=b"A8")
    kw8 = {**kw, "profile": "qwen3-8b"}
    kw8["adapters"] = {"LORA_A": good, "LORA_B": make_adapter(tmp / "b4", base="Qwen/Qwen3-4B")}
    with pytest.raises(PipelineError, match="LORA_B.*기반 모델"):
        package_adapters(**kw8)
    kw8["adapters"] = {"LORA_A": good, "LORA_B": make_adapter(tmp / "b8r", base="Qwen/Qwen3-8B", r=32)}
    with pytest.raises(PipelineError, match="LORA_B.*max_lora_rank"):
        package_adapters(**kw8)
    assert not (tmp / "registry").exists() or not list((tmp / "registry").glob("manifests/*"))   # 하나도 게시되지 않았다


def test_same_content_different_mtime_gives_same_content_hash(setup, tmp_path):
    """T22: 같은 내용을 다른 방법(복사, mtime 다름)으로 만든 두 입력은 같은 content_hash를 낸다.
    기대값은 콘텐츠 주소 지정의 정의에서 온다: 해시는 내용에만 달려야 하고 파일 시스템 mtime에 달리면 안 된다."""
    import os, shutil
    kw, tmp = setup
    kw2 = dict(kw)
    kw2["adapters"], kw2["prompts"] = {}, {}
    for role, d in kw["adapters"].items():
        c = tmp_path / "copy" / role
        shutil.copytree(d, c)                                   # 복사: mtime은 복사 시각
        for f in c.iterdir():
            os.utime(f, (1_000_000_000, 1_000_000_000))        # 2001년으로 고정해 원본과 다르게 만든다
        kw2["adapters"][role] = c
    for role, p in kw["prompts"].items():
        c = tmp_path / "copy" / f"{role}.txt"
        shutil.copyfile(p, c)
        os.utime(c, (1_100_000_000, 1_100_000_000))
        kw2["prompts"][role] = c
    kw2["out"] = tmp_path / "out2"
    kw2["publish"] = False
    kw["publish"] = False
    r1, r2 = package_adapters(**kw), package_adapters(**kw2)
    for role in r1:
        assert r1[role]["content_hash"] == r2[role]["content_hash"]
