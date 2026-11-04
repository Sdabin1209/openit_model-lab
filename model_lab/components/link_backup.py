"""models-data(모델 레지스트리 데이터) 백업·복구 잡 (계약 RUNTIME R10.1·R9.2, DATA_OWNERSHIP 백업 행, BackupJobResult).

백업: /data(MODEL_REGISTRY_ROOT)의 파일 트리를 자기 영역 게이트웨이 출구(R1.8)로 platform 백업 저장소(restic REST)에 보낸다.
복구: 스냅숏을 빈 /data에만 되살리고 매니페스트·평가 리포트·어댑터 해시를 다시 검증한다. 묶음의 내용·복구는 데이터 주인(이 영역)의 몫이다.
왜 restic: 백업 저장소 프로토콜이 계약 CommonEnv에 'restic REST 서버'(OPENIT_BACKUP_URL)로 정해져 있고 restic이 그 표준 클라이언트다(ADR 0035).
왜 복사본을 만들지 않고 /data를 바로 보내나: 게시는 어댑터·평가 리포트를 먼저 쓰고 매니페스트를 마지막에 쓴다(R10.8, model_registry._s_commit). restic은 디렉터리를 이름 순으로 읽어
adapters, eval_reports, ids.json, manifests 순이므로 스냅숏에 매니페스트가 있으면 그 어댑터·리포트는 이미 들어 있다. 어댑터가 수 GB일 수 있어 복사본(디스크 2배)을 피한다.
비밀(백업 자격·저장소 암호)은 파일로 받고 어떤 출력·로그에도 쓰지 않는다(D84). 멱등: 다시 실행하면 새 스냅숏이 하나 더 생길 뿐이다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote, urlsplit, urlunsplit

from boundary_contracts import BoundaryError, read_secret, validator
from boundary_contracts.clients import egress_proxy_from, require_tls_url


EX_TEMPFAIL = 75      # 재시도 가능(BackupJobResult: 백업 저장소에 닿지 못함)
# 보존: restic 문서의 표준 정책 형식. 값은 시작값이며 측정하지 않았다(ADR 0035). 모델 패키지는 불변이라 최근 스냅숏이 이전 것을 포함한다.
KEEP = ("--keep-daily", "7", "--keep-weekly", "4", "--keep-monthly", "6")
# 시간 한도: restic은 닿지 않는 저장소를 기본으로 오래(분 단위) 재시도하며 기다린다. 잡이 매달리지 않게 저장소 확인(cat config·init)에는 짧은 한도를 건다(시작값 60초, 측정하지 않았다).
# 본 작업(backup·restore·forget)의 한도는 deploy-jobs.json의 timeout_seconds(3600) 안에서 deploy가 잡을 멈추므로 여기서는 그보다 조금 짧은 3300초로 둔다(ADR 0035).
PROBE_TIMEOUT = 60
WORK_TIMEOUT = 3300
EXCLUDES = (".tmp", ".lock")      # 게시 중 임시 폴더와 잠금 파일은 데이터가 아니다


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repo_url(url: str, credentials: str) -> str:
    """restic REST 주소 rest:https://<사용자>:<암호>@호스트/경로 (restic 문서의 표준 형식, RFC 7617 자격). https만 허용한다(loopback만 http)."""
    parts = urlsplit(url)
    try:
        require_tls_url(url)      # R1.1
    except ValueError:
        raise BoundaryError("INVALID_RECORD", "OPENIT_BACKUP_URL은 https 주소여야 한다(loopback만 http)") from None
    if not parts.hostname:
        raise BoundaryError("INVALID_RECORD", "OPENIT_BACKUP_URL이 주소가 아니다")
    user, _, pw = credentials.strip().partition(":")
    netloc = f"{quote(user, safe='')}:{quote(pw, safe='')}@{parts.netloc}"
    return "rest:" + urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


def _restic_env(environ, stage_root: str) -> tuple[dict, Path]:
    """(restic에 주는 환경, 데이터 폴더). 필요한 것만 넘긴다(R9.1)."""
    url = environ.get("OPENIT_BACKUP_URL")
    if not url:
        raise BoundaryError("INVALID_RECORD", "OPENIT_BACKUP_URL이 필요하다")
    creds = read_secret("OPENIT_BACKUP_CREDENTIALS", environ=environ)
    pw_file = environ.get("OPENIT_BACKUP_REPO_PASSWORD_FILE")
    if not creds or not pw_file:
        raise BoundaryError("INVALID_RECORD", "OPENIT_BACKUP_CREDENTIALS_FILE과 OPENIT_BACKUP_REPO_PASSWORD_FILE이 필요하다")
    env = {"PATH": environ.get("PATH", ""), "RESTIC_REPOSITORY": _repo_url(url, creds), "RESTIC_PASSWORD_FILE": pw_file,
           "RESTIC_CACHE_DIR": os.path.join(stage_root, ".restic-cache")}      # 홈이 없는 비root 사용자에서도 돌도록 캐시를 임시 폴더 아래에
    if "SSL_CERT_FILE" in environ:
        env["SSL_CERT_FILE"] = environ["SSL_CERT_FILE"]      # 사설 루트를 표준 변수로 믿는다
    proxy = egress_proxy_from(environ)
    if proxy:
        env["HTTPS_PROXY"] = proxy      # 자기 영역 게이트웨이의 CONNECT 출구(R1.8). restic은 표준 HTTPS_PROXY를 읽는다
    return env, Path(environ.get("MODEL_REGISTRY_ROOT") or "/data")


def _call(runner, cmd, timeout, **kw):
    """restic 호출. 시간 한도를 넘으면 닿지 못한 것(UNAVAILABLE, 재시도 가능)으로 본다. 출력 문구는 싣지 않는다."""
    try:
        return runner(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    except subprocess.TimeoutExpired:
        raise BoundaryError("UNAVAILABLE", "백업 저장소가 시간 안에 응답하지 않았다") from None


def _summary(stdout: str):
    summary = None
    for line in stdout.splitlines():
        try:
            j = json.loads(line)
        except ValueError:
            continue
        if j.get("message_type") == "summary":
            summary = j
    return summary


def _failed(e: BoundaryError, started: str, now) -> tuple:
    # 메시지는 우리가 쓴 고정 문구라 비밀이 없다(restic의 출력은 싣지 않는다)
    err = {"code": e.code, "message": e.message, "retryable": e.retryable}
    return ({"status": "failed", "snapshot_id": None, "bytes": 0, "started_at": started, "finished_at": now(), "error": err},
            EX_TEMPFAIL if e.retryable else 1)


def _unexpected(e: Exception, started: str, now) -> tuple:
    err = {"code": "UNAVAILABLE", "message": f"예기치 못한 예외: {type(e).__name__}", "retryable": True}      # 예외 문구는 싣지 않는다
    return {"status": "failed", "snapshot_id": None, "bytes": 0, "started_at": started, "finished_at": now(), "error": err}, EX_TEMPFAIL


def run(environ, *, runner: Optional[Callable] = None, now: Callable[[], str] = _ts) -> tuple:
    """(BackupJobResult 레코드, 종료 코드). 0 ok, 75 재시도 가능(백업 저장소에 닿지 못함), 1 그 밖의 실패."""
    runner = runner or subprocess.run
    started = now()
    stage = tempfile.mkdtemp(prefix=".backup-")
    try:
        env, data = _restic_env(environ, stage)
        if not (data / "manifests").is_dir():
            raise BoundaryError("FAILED_PRECONDITION", "백업할 데이터 폴더가 레지스트리 모양이 아니다")
        if _call(runner, ["restic", "cat", "config"], PROBE_TIMEOUT, env=env).returncode != 0:      # 저장소가 없으면 만든다(멱등)
            if _call(runner, ["restic", "init"], PROBE_TIMEOUT, env=env).returncode != 0:
                raise BoundaryError("UNAVAILABLE", "백업 저장소에 닿지 못했다")
        excl = [a for x in EXCLUDES for a in ("--exclude", x)]
        r = _call(runner, ["restic", "backup", "--json", *excl, "."], WORK_TIMEOUT, env=env, cwd=str(data))
        if r.returncode != 0:
            raise BoundaryError("UNAVAILABLE", "restic backup이 실패했다")
        summary = _summary(r.stdout)
        if summary is None:
            raise BoundaryError("INVALID_RECORD", "restic이 요약을 내지 않았다")
        if _call(runner, ["restic", "forget", "--prune", *KEEP], WORK_TIMEOUT, env=env).returncode != 0:
            raise BoundaryError("UNAVAILABLE", "restic forget --prune이 실패했다")
        return ({"status": "ok", "snapshot_id": str(summary["snapshot_id"])[:128], "bytes": int(summary.get("total_bytes_processed", 0)),
                 "started_at": started, "finished_at": now()}, 0)
    except BoundaryError as e:
        return _failed(e, started, now)
    except Exception as e:      # noqa: BLE001
        return _unexpected(e, started, now)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _empty(data: Path) -> bool:
    """복구는 빈 레지스트리에만: 게시된 패키지(매니페스트)가 하나도 없다."""
    return not list((data / "manifests").rglob("*.json")) if (data / "manifests").is_dir() else True


def restore(environ, *, verify: Callable[[Path], int], snapshot: str = "latest", runner: Optional[Callable] = None, now: Callable[[], str] = _ts) -> tuple:
    """verify(데이터 폴더): 복구한 데이터를 읽어 해시를 검증하고 실패하면 예외를 던진다(레지스트리 파일 구현은 진입점이 주입한다. 컴포넌트끼리 import하지 않는다).
    (BackupJobResult 레코드, 종료 코드). 스냅숏(ID 또는 latest)을 빈 데이터 폴더로 복구하고 해시를 다시 검증한다.
    `bytes`는 복구한 바이트 수. 비어 있지 않으면 거부(종료 코드 1). 검증이 실패하면 이번에 쓴 것을 지운다(전부 또는 아무것도)."""
    runner = runner or subprocess.run
    started = now()
    stage = tempfile.mkdtemp(prefix=".restore-")
    written: list[Path] = []
    try:
        if not (snapshot == "latest" or (snapshot.isalnum() and 4 <= len(snapshot) <= 64)):
            raise BoundaryError("INVALID_RECORD", "스냅숏은 ID 또는 latest여야 한다")
        env, data = _restic_env(environ, stage)
        if not _empty(data):
            raise BoundaryError("FAILED_PRECONDITION", "복구 대상 데이터 폴더가 비어 있지 않다")
        out = Path(stage) / "out"
        r = _call(runner, ["restic", "restore", snapshot, "--target", str(out), "--json"], WORK_TIMEOUT, env=env)
        if r.returncode != 0:
            raise BoundaryError("UNAVAILABLE", "restic restore가 실패했다(스냅숏이 없거나 백업 저장소에 닿지 못했다)")
        roots = sorted({p.parent for p in out.rglob("manifests") if p.is_dir()})
        if len(roots) != 1:
            raise BoundaryError("INVALID_RECORD", "스냅숏에서 레지스트리 폴더를 하나로 찾지 못했다")
        src, total = roots[0], 0
        data.mkdir(parents=True, exist_ok=True)
        try:
            for item in sorted(src.iterdir()):
                dest = data / item.name
                if dest.exists() and item.is_dir() and not any(dest.iterdir()):
                    dest.rmdir()
                if dest.exists():
                    raise BoundaryError("FAILED_PRECONDITION", "복구 대상 데이터 폴더가 비어 있지 않다")
                written.append(dest)
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)
            total = sum(f.stat().st_size for w in written for f in ([w] if w.is_file() else w.rglob("*")) if f.is_file())
            verify(data)
        except BaseException:
            for w in reversed(written):      # 되돌린다: 복구는 전부 되살리거나 아무것도 남기지 않는다
                shutil.rmtree(w, ignore_errors=True) if w.is_dir() else w.unlink(missing_ok=True)
            raise
        sid = str((_summary(r.stdout) or {}).get("snapshot_id") or snapshot)[:128]
        return {"status": "ok", "snapshot_id": sid, "bytes": int(total), "started_at": started, "finished_at": now()}, 0
    except BoundaryError as e:
        return _failed(e, started, now)
    except Exception as e:      # noqa: BLE001
        return _unexpected(e, started, now)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main(argv=None, environ=None) -> int:
    """`python -m model_lab.service backup`: 마지막 줄 JSON(BackupJobResult), 종료 코드 0·75·1. 내보내기 전에 계약으로 검증한다(R7)."""
    out, rc = run(os.environ if environ is None else environ)
    validator("BackupJobResult").validate(out)
    print(json.dumps(out, ensure_ascii=False))
    return rc


def restore_main(argv=None, environ=None, *, verify: Callable[[Path], int]) -> int:
    """`python -m model_lab.service restore [--snapshot <ID|latest>]`: 마지막 줄 JSON(BackupJobResult), 종료 코드 0·75·1."""
    argv = list(argv or [])
    snap = "latest"
    if argv[:1] == ["--snapshot"] and len(argv) == 2:
        snap = argv[1]
    elif argv:
        print("사용: python -m model_lab.service restore [--snapshot <ID|latest>]", file=sys.stderr)
        return 2
    out, rc = restore(os.environ if environ is None else environ, verify=verify, snapshot=snap)
    validator("BackupJobResult").validate(out)
    print(json.dumps(out, ensure_ascii=False))
    return rc
