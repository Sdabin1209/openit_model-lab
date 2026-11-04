"""RUNTIME R10.1 + 계약 DATA_OWNERSHIP 백업 행: models-data 백업·복구 잡. 선언은 계약 validator로, 왕복은 실제 restic + 표준 restic rest-server(컨테이너)로 시험한다.
이 시험이 띄우는 것: rest-server 컨테이너 하나(이름 model-lab-restsrv-<무작위>)와 restic 바이너리를 꺼내려고 잠깐 만드는 컨테이너 하나. 둘 다 이 시험이 지운다.
docker가 없으면 왕복 시험은 건너뛴다(통과로 세지 않는다)."""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid

import pytest
from boundary_contracts import validator

from model_lab.components import link_backup as bk
from model_lab.components.model_registry import FileModelRegistry
from model_lab.wiring.backup_verify import verify_data
from tests.conftest import HANG_GUARD_SECONDS, ROOT
from tests.test_registry import make

# 시험이 쓰는 컨테이너 이미지는 태그가 아니라 digest로 고정한다(:latest는 시간이 지나면 다른 이미지가 되어 시험이 재현되지 않는다).
# digest는 2026-11-05에 이 PC에서 받아 확인한 것(restic 0.19.1, rest-server 0.14.0). 올리려면 새 digest로 바꾸고 시험을 다시 돌린다. 표준: OCI 이미지 digest 참조(name@sha256:...).
RESTIC_IMAGE = "restic/restic@sha256:9940e6c7421ab3a21ee9fdc4e91596d04c792b600d31b102696c75cbf3ac4481"
REST_SERVER_IMAGE = "restic/rest-server@sha256:d2aff06f47eb38637dff580c3e6bce4af98f386c396a25d32eb6727ec96214a5"
USER, PW = "backupuser", "pw-restic-canary-model-lab"
REPO_PW = "repo-password-canary"
JOBS = json.loads((ROOT / "deploy-jobs.json").read_text(encoding="utf-8"))
EXPOSURE = json.loads((ROOT / "deploy-exposure.json").read_text(encoding="utf-8"))


# ---- 선언 ----
def test_jobs_declaration_is_valid_and_matches_the_entry_point():
    assert list(validator("JobsDeclaration").iter_errors(JOBS)) == []
    jobs = {j["name"]: j for j in JOBS["jobs"]}
    assert set(jobs) == {"backup", "restore"}
    for name, j in jobs.items():
        assert j["argv"] == ["python", "-m", "model_lab.service", name] and j["idempotent"] is True
        assert j["needs"] == ["models-data"] and isinstance(j["timeout_seconds"], int)
        assert set(j["secret_files"]) == {"OPENIT_BACKUP_CREDENTIALS_FILE", "OPENIT_BACKUP_REPO_PASSWORD_FILE"}
        assert set(j["env"]) == {"OPENIT_BACKUP_URL", "OPENIT_EGRESS_PROXY"}


def test_exposure_lets_the_backup_out_through_the_area_gateway_only():
    assert "external:backup" in EXPOSURE["egress"] and list(validator("Exposure").iter_errors(EXPOSURE)) == []


def _run_entry(*args, env=None):
    e = {"PATH": os.environ["PATH"], **(env or {})}
    r = subprocess.run([sys.executable, "-m", "model_lab.service", *args], capture_output=True, text=True, cwd=ROOT, env=e, timeout=HANG_GUARD_SECONDS)
    return r


def test_entry_point_jobs_print_a_valid_last_line_and_use_exit_codes():
    r = _run_entry("backup")      # 필수 입력이 없다: 재시도로 풀리지 않는 실패 = 종료 코드 1, 마지막 줄은 BackupJobResult
    out = json.loads(r.stdout.strip().splitlines()[-1])
    validator("BackupJobResult").validate(out)
    assert r.returncode == 1 and out["status"] == "failed" and out["snapshot_id"] is None and out["error"]["retryable"] is False
    r2 = _run_entry("restore", "--snapshot", "latest")
    assert r2.returncode == 1 and json.loads(r2.stdout.strip().splitlines()[-1])["status"] == "failed"
    assert _run_entry("no-such-job").returncode == 2 and _run_entry("restore", "--bogus").returncode == 2


# ---- 왕복: 실제 restic + rest-server ----
def _wait(cond, limit=60):
    """외부 컨테이너 기동을 기다린다(우리 코드의 시간이 아니라 rest-server 프로세스의 준비). 상한 안에 안 되면 실패."""
    import time
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.3)
    return False



def _docker(*a, check=True):
    return subprocess.run(["docker", *a], capture_output=True, text=True, check=check, timeout=HANG_GUARD_SECONDS)


@pytest.fixture(scope="module")
def restic_dir(tmp_path_factory):
    if shutil.which("docker") is None or _docker("info", check=False).returncode != 0:
        pytest.skip("docker 없음")
    d = tmp_path_factory.mktemp("restic")
    name = f"model-lab-restic-extract-{uuid.uuid4().hex[:8]}"
    _docker("create", "--name", name, RESTIC_IMAGE)
    try:
        _docker("cp", f"{name}:/usr/bin/restic", str(d / "restic"))
    finally:
        _docker("rm", "-f", name, check=False)
    (d / "restic").chmod(0o755)
    return str(d)


@pytest.fixture
def rest_url(tmp_path):
    data = tmp_path / "repo"
    data.mkdir(); data.chmod(0o777)
    (data / ".htpasswd").write_text(f"{USER}:{{SHA}}{base64.b64encode(hashlib.sha1(PW.encode()).digest()).decode()}\n")      # rest-server htpasswd 인증(append-only 아님: forget --prune이 필요하다)
    (data / ".htpasswd").chmod(0o644)
    name = f"model-lab-restsrv-{uuid.uuid4().hex[:8]}"
    _docker("run", "-d", "--name", name, "-p", "127.0.0.1::8000", "-v", f"{data}:/data", "--user", f"{os.getuid()}:{os.getgid()}", "--entrypoint", "rest-server", REST_SERVER_IMAGE, "--path", "/data")
    try:
        assert _wait(lambda: "Authentication enabled" in _docker("logs", name, check=False).stdout + _docker("logs", name, check=False).stderr)
        port = _docker("port", name, "8000/tcp").stdout.split(":")[-1].strip()
        yield f"http://127.0.0.1:{port}/model-lab"      # 평문 http는 loopback에서만 허용된다(R1.1)
        _docker("stop", "-t", "2", name, check=False)
        foreign = [str(f) for f in [data, *data.rglob("*")] if f.lstat().st_uid != os.getuid()]
        assert foreign == [], f"컨테이너가 다른 사용자 소유 파일을 남겼다: {foreign[:3]}"
    finally:
        _docker("rm", "-f", name, check=False)


def _env(tmp_path, restic_dir, rest_url, root):
    (tmp_path / "cred").write_text(f"{USER}:{PW}"); (tmp_path / "pw").write_text(REPO_PW)
    return {"OPENIT_BACKUP_URL": rest_url, "OPENIT_BACKUP_CREDENTIALS_FILE": str(tmp_path / "cred"), "OPENIT_BACKUP_REPO_PASSWORD_FILE": str(tmp_path / "pw"),
            "MODEL_REGISTRY_ROOT": str(root), "PATH": f"{restic_dir}:/usr/bin:/bin"}


def _populate(root, tmp_path):
    reg = FileModelRegistry(root)
    made = {}
    for pid, ver, payload in (("pkg-a", "v1", b"weights-a1"), ("pkg-a", "v2", b"weights-a2"), ("pkg-b", "v1", b"weights-b1")):
        m, ad, rep = make(tmp_path, pid=pid, ver=ver, payload=payload, ref=f"eval-{pid}-{ver}", report=b"report " + payload)
        reg.publish(m, ad, rep)
        made[(pid, ver)] = m
    (root / ".tmp").mkdir(exist_ok=True); (root / ".tmp" / "partial-upload").write_text("junk")      # 게시 중 임시 폴더는 데이터가 아니다
    return made


def test_backup_then_restore_round_trip_with_real_restic_and_rest_server(tmp_path, restic_dir, rest_url, capsys):
    src, dst = tmp_path / "src", tmp_path / "dst"
    made = _populate(src, tmp_path)
    env_src, env_dst = _env(tmp_path, restic_dir, rest_url, src), _env(tmp_path, restic_dir, rest_url, dst)
    out, rc = bk.run(env_src)
    validator("BackupJobResult").validate(out)
    assert rc == 0 and out["status"] == "ok" and len(out["snapshot_id"]) >= 8 and out["bytes"] > 0, out
    out2, rc2 = bk.run(env_src)      # 다시 실행: 새 스냅숏이 하나 더 생긴다(멱등)
    assert rc2 == 0 and out2["snapshot_id"] != out["snapshot_id"]
    res, rcr = bk.restore(env_dst, verify=verify_data, snapshot="latest")
    validator("BackupJobResult").validate(res)
    assert rcr == 0 and res["status"] == "ok" and res["bytes"] > 0, res
    reg = FileModelRegistry(dst)
    assert sorted(reg.list_ids()[0]) == ["pkg-a", "pkg-b"] and reg.list_versions("pkg-a") == ["v1", "v2"]
    for (pid, ver), m in made.items():      # 복구한 매니페스트·어댑터·리포트가 원본과 같다
        assert reg.get_manifest(pid, ver) == m and reg.get_eval_report(m["eval_report_ref"]).startswith(b"report ")
        assert reg.open_adapter(pid, ver, dest=tmp_path / "open").exists()
    assert not (dst / ".tmp" / "partial-upload").exists()      # 제외한 것은 스냅숏에 없다
    # 비밀·저장소 주소는 어떤 결과에도 없다
    text = json.dumps([out, out2, res])
    assert PW not in text and REPO_PW not in text and rest_url not in text


def test_restore_refuses_a_non_empty_registry_and_is_not_retryable(tmp_path, restic_dir, rest_url):
    src = tmp_path / "src"
    _populate(src, tmp_path)
    env = _env(tmp_path, restic_dir, rest_url, src)
    first = bk.run(env)
    assert first[1] == 0, first
    out, rc = bk.restore(env, verify=verify_data, snapshot="latest")      # 같은 폴더(비어 있지 않다)로 복구 시도
    assert rc == 1 and out["status"] == "failed" and out["error"]["code"] == "FAILED_PRECONDITION" and out["error"]["retryable"] is False
    assert FileModelRegistry(src).list_versions("pkg-a") == ["v1", "v2"]      # 원본은 그대로


def test_restore_by_snapshot_id_and_missing_snapshot_and_unreachable_store(tmp_path, restic_dir, rest_url, monkeypatch):
    src, dst, dst2 = tmp_path / "src", tmp_path / "dst", tmp_path / "dst2"
    _populate(src, tmp_path)
    env_src = _env(tmp_path, restic_dir, rest_url, src)
    first, _ = bk.run(env_src)
    ok, rc = bk.restore(_env(tmp_path, restic_dir, rest_url, dst), verify=verify_data, snapshot=first["snapshot_id"])
    assert rc == 0 and ok["status"] == "ok"      # 스냅숏 ID로도 복구한다
    miss, rc2 = bk.restore(_env(tmp_path, restic_dir, rest_url, dst2), verify=verify_data, snapshot="deadbeefdeadbeef")
    assert rc2 == 75 and miss["error"]["retryable"] is True and not dst2.exists() or not list(dst2.rglob("*.json"))      # 없는 스냅숏: 재시도 가능, 아무것도 안 만든다
    monkeypatch.setattr(bk, "PROBE_TIMEOUT", 5)      # 닿지 않는 저장소: restic은 오래 재시도하므로 확인 한도(기본 60초)로 끊는다
    down = dict(env_src, OPENIT_BACKUP_URL="http://127.0.0.1:9/model-lab")
    out, rc3 = bk.run(down)
    assert rc3 == 75 and out["status"] == "failed" and out["error"]["code"] == "UNAVAILABLE" and out["error"]["retryable"] is True


def test_restore_that_fails_verification_leaves_nothing_behind(tmp_path, restic_dir, rest_url, monkeypatch):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _populate(src, tmp_path)
    first = bk.run(_env(tmp_path, restic_dir, rest_url, src))
    assert first[1] == 0, first

    def bad(data):
        raise RuntimeError("hash mismatch")
    out, rc = bk.restore(_env(tmp_path, restic_dir, rest_url, dst), verify=bad, snapshot="latest")
    assert rc == 75 and out["status"] == "failed"
    assert not list(dst.rglob("*.json")) and not (dst / "adapters").exists() and not (dst / "ids.json").exists()      # 전부 또는 아무것도


def test_non_https_remote_backup_url_is_refused_without_calling_restic(tmp_path):
    calls = []
    env = {"OPENIT_BACKUP_URL": "http://backup.example/repo", "OPENIT_BACKUP_CREDENTIALS": "u:p", "OPENIT_BACKUP_REPO_PASSWORD_FILE": str(tmp_path / "pw"),
           "MODEL_REGISTRY_ROOT": str(tmp_path)}
    out, rc = bk.run(env, runner=lambda *a, **k: calls.append(a))
    assert rc == 1 and out["error"]["code"] == "INVALID_RECORD" and calls == []      # R1.1: loopback 밖 평문은 거부
