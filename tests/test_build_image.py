"""U42: 영역 빌드 진입점 scripts/build-image.sh(계약 BuildImageRequest / BuildImageResult, ADR 0024)와 ServingConfig 계약 검증."""
import copy
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from boundary_contracts import validator

from model_lab.config import validate_config
from model_lab.errors import ConfigError
from tests.conftest import ROOT

SCRIPT = ROOT / "scripts" / "build-image.sh"
BC_REPO = Path(os.environ.get("OPENIT_BC_REPO", "/home/user/sdabin/healthKiosk/boundary-contracts"))     # 읽기 전용(워크스페이스가 허용한 이웃)
LOG_DIR = Path.home() / "openit-logs"


# ---- ServingConfig: 계약 스키마로 검증(model-runtime 사본 대신) ---------------------------------------------------------------

def test_config_rejects_serving_config_that_breaks_the_contract(cfg):
    """계약 ServingConfig는 모르는 키를 거부한다(해시가 달라지므로). 설정을 읽는 시점에 거절되어야 한다."""
    bad = copy.deepcopy({k: v for k, v in cfg.items() if k != "_base_dir"})
    bad["serving"]["LORA_A"]["unknown_key"] = 1
    with pytest.raises(ConfigError, match="serving"):
        validate_config(bad)
    del bad["serving"]["LORA_A"]["unknown_key"]
    validate_config(bad)                                    # 원래대로면 통과한다


# ---- 진입점 ------------------------------------------------------------------------------------------------------------------

def bc_source(tmp_path, tag="v2.6.0") -> Path:
    """deploy가 하는 것처럼 태그 시점 소스 트리를 꺼낸다(git archive)."""
    d = tmp_path / "bc-src"
    d.mkdir(exist_ok=True)
    tar = subprocess.run(["git", "-C", str(BC_REPO), "archive", tag], capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(d)], input=tar, check=True)
    return d


def request(tmp_path, **over):
    req = {"source_dir": str(ROOT), "revision": "abcdef1", "versions": {"boundary": "v2.6.0"},
           "dependency_sources": {"boundary": str(bc_source(tmp_path))}, "image_tag": f"model-lab-registry-test/{uuid.uuid4().hex[:8]}:t"}
    req.update(over)
    p = tmp_path / "req.json"
    p.write_text(json.dumps(req))
    return req, p


def run(p, **env):
    return subprocess.run(["bash", str(SCRIPT), str(p)], cwd=ROOT, capture_output=True, text=True, env={**os.environ, **env}, timeout=550)


@pytest.mark.parametrize("mutate,why", [
    (lambda r: r.update(unknown_key=1), "모르는 키"),
    (lambda r: r.pop("revision"), "필요한 키 없음"),
    (lambda r: r["versions"].pop("boundary"), "versions.boundary 없음"),
    (lambda r: r.update(revision="not-hex"), "revision 형식"),
    (lambda r: r.pop("dependency_sources"), "이 영역이 쓰는 dependency_sources.boundary 없음"),
    (lambda r: r.update(variant="gpu"), "모르는 variant"),
])
def test_bad_requests_fail_before_docker(tmp_path, mutate, why):
    req, _ = request(tmp_path)
    mutate(req)
    p = tmp_path / "bad.json"; p.write_text(json.dumps(req))
    r = run(p, DOCKER="/nonexistent/docker")                # docker를 부르면 다른 이유로 실패하므로 검증 실패와 구분된다
    assert r.returncode != 0 and "nonexistent" not in r.stderr, why


@pytest.mark.skipif(shutil.which("docker") is None or not BC_REPO.is_dir(), reason="docker 또는 boundary-contracts 리포 없음")
def test_build_image_end_to_end(tmp_path):
    """실제 docker 빌드 한 번(스모크). 결과 마지막 줄이 BuildImageResult를 통과하고 라벨이 요청 값과 같다."""
    req, p = request(tmp_path)
    r = run(p)
    try:
        assert r.returncode == 0, r.stderr[-2000:]
        out = json.loads(r.stdout.strip().splitlines()[-1])
        validator("BuildImageResult").validate(out)
        assert out["image"] == req["image_tag"]
        lab = out["labels"]
        assert lab["io.openit.revision"] == req["revision"] and lab["io.openit.contracts.boundary"] == "v2.6.0"
        assert lab["io.openit.service"] == "model-registry"
        # U47: 노출 선언 라벨. 값은 deploy-exposure.json과 같고 계약 Exposure를 통과한다
        exposure = json.loads(lab["io.openit.exposure"])
        validator("Exposure").validate(exposure)
        assert exposure == json.loads((ROOT / "deploy-exposure.json").read_text(encoding="utf-8"))
        again = run(p)                                       # 멱등: 같은 요청은 같은 이미지(wheel 안 시각을 고정해 두었다)
        assert again.returncode == 0 and json.loads(again.stdout.strip().splitlines()[-1])["image_id"] == out["image_id"]
        LOG_DIR.mkdir(exist_ok=True)
        (LOG_DIR / "model-lab-build-image-last.json").write_text(r.stdout.strip().splitlines()[-1] + "\n")
    finally:
        subprocess.run(["docker", "rmi", "-f", req["image_tag"]], capture_output=True)
