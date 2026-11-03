"""학습 서버 정본 코드를 감싸는 얇은 층 (D78, G06).

학습의 정본은 학습 서버의 코드(`train_lora_a.py`, `train_lora_b.py`)다. 이 파일은 그 코드를 복사하거나 다시 구현하지 않는다.
하는 일은 다섯 가지뿐이다: 설정 검증, 서버 스크립트를 부르는 명령 조립, (GPU 서버에서) 실행, 산출물 확인, 실행 기록.
서버 스크립트가 받는 인자 이름은 설정의 `server.arg_map`에 둔다(코드에 하드코딩하지 않는다).
`server.args_verified`는 "인자 이름을 서버 스크립트 근거로 확인했다"는 뜻이다(`--help` 실행 여부와는 별개). 근거는 `server.args_evidence`에 글로 남기고
doctor가 그대로 보여 준다. false인 동안은 확인하지 못한 값이므로 doctor가 구멍으로 표시한다.
값 규칙: 불리언 하이퍼파라미터는 값 없는 플래그다. true면 플래그만 넘기고 false면 넘기지 않는다(예: `--dry_run`).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from ..wiring.errors import PipelineError

SPLITS = ("train", "val", "test")


def _p(cfg: dict, value: str) -> Path:
    """설정의 경로. ~는 풀고 상대 경로는 설정 파일 위치 기준."""
    p = Path(os.path.expanduser(value))
    return p if p.is_absolute() else (Path(cfg["_base_dir"]) / p).resolve()


def script_path(cfg: dict) -> Path:
    return _p(cfg, cfg["server"]["code_dir"]) / cfg["server"]["scripts"][cfg["role"]]


def dataset_files(cfg: dict) -> dict[str, Path]:
    d, pre = _p(cfg, cfg["dataset"]["dir"]), cfg["dataset"]["prefix"]
    return {s: d / f"{pre}_{s}.jsonl" for s in SPLITS}


def adapter_dir(cfg: dict) -> Path:
    return _p(cfg, cfg["output"]["adapter_dir"])


def record_path(cfg: dict) -> Path:
    rf = cfg["output"].get("record_file")
    a = adapter_dir(cfg)
    return _p(cfg, rf) if rf else a.parent / f"{a.name}.train-wrap.json"


def _fmt(v) -> str:
    return str(v).lower() if isinstance(v, bool) else str(v)


# ---- 원격(ssh) 실행 (G06): server.ssh가 있으면 학습 서버의 스크립트를 ssh로 부른다. 스크립트는 복사하지 않는다 ----
# 왜 BatchMode/ConnectTimeout: 비밀번호 질문으로 멈추지 않고 서버가 꺼져 있으면 빨리 실패한다(워크스페이스 ssh 관례, 측정한 값 아님).
# 왜 ServerAliveInterval=60: 수 시간 걸리는 학습 중 유휴 NAT가 연결을 끊는 것을 줄인다. 60초는 ssh 문서 예시의 관례값이며 측정하지 않았다(ADR 0030).
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
SSH_LONG_OPTS = ["-o", "ServerAliveInterval=60"]


# 서버에서 쓰기·수정·삭제가 허락된 곳(사용자 결정 2026-11-03): ~/dabin 아래뿐이다. 그 밖은 읽기와 ~/dabin 안으로의 복사만 한다.
# 예외: 모델 캐시는 서버 기본 위치(~/.cache/huggingface)를 쓴다(사용자 결정). HF 캐시는 내용 해시로 새 파일만 추가되고 이미 있는 파일은 읽기만 한다:
# 그래서 HF_HOME을 덮어쓰지 않고, 덮어쓰기·정리 옵션(force_download, 캐시 삭제, revision 덮어쓰기)을 실행 명령에 넣지 않는다(시험이 확인).
SANDBOX_PREFIX = "~/dabin/"


def remote(cfg: dict) -> str | None:
    """ssh 호스트(~/.ssh/config의 이름). 없으면 이 컴퓨터가 학습 서버다."""
    return (cfg["server"].get("ssh") or {}).get("host")


def _rq(path: str) -> str:
    """원격 셸용 인용. 앞의 ~/는 서버 사용자 홈으로 풀리게 둔다."""
    if path == "~" or path.startswith("~/"):
        return '"$HOME"' + shlex.quote(path[1:])
    return shlex.quote(path)


def _rjoin(argv: list[str]) -> str:
    return " ".join(_rq(a) if a.startswith("~/") else shlex.quote(a) for a in argv)


def ssh_run(cfg: dict, remote_cmd: str, *, binary=False, timeout=60) -> subprocess.CompletedProcess:
    """원격 명령을 한 번 실행한다. 종료 코드 255는 ssh 연결 실패다."""
    return subprocess.run(["ssh", *SSH_OPTS, remote(cfg), remote_cmd], capture_output=True, text=not binary, timeout=timeout)


def _srv(cfg: dict, key: str) -> str:
    """서버 쪽 경로 문자열(원격이면 설정 값 그대로, 로컬이면 풀어낸 경로)."""
    raw = {"code_dir": cfg["server"]["code_dir"], "data_dir": cfg["dataset"]["dir"]}[key]
    return raw if remote(cfg) else str(_p(cfg, raw))


def sandbox_paths(cfg: dict) -> dict[str, str]:
    """원격 실행이 쓰는 모든 서버 쪽 경로(쓰기). 정본 스크립트 사본, 임시, 그래프 설정, 데이터 업로드, 출력."""
    root = (cfg["server"].get("sandbox_root") or "").rstrip("/")
    out = {"sandbox_root": root, "code(스크립트 사본)": root + "/code", "TMPDIR": root + "/tmp", "MPLCONFIGDIR": root + "/mpl",
           "output.server_out_dir": cfg["output"].get("server_out_dir") or ""}
    if cfg["dataset"].get("upload_root"):
        out["dataset.upload_root"] = cfg["dataset"]["upload_root"]
    return out


def sandbox_violations(cfg: dict) -> list[str]:
    """~/dabin 아래가 아닌 쓰기 경로. 원격이 아니면 없다. `..`로 빠져나가는 경로도 위반이다."""
    if not remote(cfg):
        return []
    bad = []
    if not cfg["server"].get("sandbox_root"):
        return ["server.sandbox_root가 없다(server.ssh를 쓰면 서버에서 쓸 폴더를 ~/dabin 아래로 적어야 한다)"]
    for name, p in sandbox_paths(cfg).items():
        parts = p.split("/")
        if not p.startswith(SANDBOX_PREFIX) or ".." in parts or "" in parts[1:] or "." in parts:
            bad.append(f"{name}={p or '(없음)'}")
    return bad


def gpu_busy_state(cfg: dict) -> tuple[str, str]:
    """(상태, 설명). 상태: FREE | BUSY | UNKNOWN. 다른 프로세스가 GPU 계산을 쓰는지 `nvidia-smi --query-compute-apps`로 본다.
    서버는 여러 사람이 함께 쓰므로 남의 작업이 돌고 있으면 실행하지 않는다(남의 작업은 중단하지 않는다, 사용자 의도 2026-11-03)."""
    q = "nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader"
    try:
        if remote(cfg):
            r = ssh_run(cfg, q)
        else:
            exe = shutil.which("nvidia-smi")
            if not exe:
                return "UNKNOWN", "nvidia-smi가 없다"
            r = subprocess.run([exe, *q.split()[1:]], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN", "GPU 사용 프로세스를 조회하지 못했다"
    if r.returncode != 0:
        return "UNKNOWN", f"GPU 사용 프로세스 조회 실패(종료 코드 {r.returncode})"
    apps = [x.strip() for x in r.stdout.splitlines() if x.strip()]
    if apps:
        return "BUSY", f"다른 프로세스 {len(apps)}개가 GPU를 쓰고 있다(예: {apps[0][:80]}). 비워질 때까지 실행하지 않는다. 남의 작업은 중단하지 않는다"
    return "FREE", "GPU를 쓰는 다른 프로세스가 없다"


def _df_free_gb(cfg: dict, path_expr: str) -> float | None:
    """서버에서 path_expr(원격 셸 식)가 놓인 파일시스템의 여유(GB). 없는 폴더면 가장 가까운 존재하는 상위 폴더로 본다."""
    r = ssh_run(cfg, f'd={path_expr}; while [ ! -d "$d" ] && [ "$d" != / ]; do d=$(dirname "$d"); done; df -Pk -- "$d" | tail -1')
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.split()[3]) / 1e6
    except (IndexError, ValueError):
        return None


def disk_state(cfg: dict) -> tuple[str, str]:
    """(상태, 설명). 상태: OK | LOW | UNKNOWN. ~/dabin이 있는 파일시스템과 HF 모델 캐시 파일시스템의 여유가 (업로드+출력+모델)의 2배 이상이어야 한다.
    왜 2배: 체크포인트·임시 파일·내려받는 도중의 중복을 위한 여유이며 측정한 값이 아니라 사용자 지시다."""
    ex = cfg["output"].get("expected_gb")
    if not ex:
        return "UNKNOWN", "output.expected_gb(예상 크기)가 없다"
    need = 2 * (ex["upload"] + ex["output"] + ex["model_download"])
    spots = {"~/dabin": _rq(SANDBOX_PREFIX.rstrip("/")), "HF 모델 캐시": '"$HOME"/.cache/huggingface'}
    msgs, low = [], False
    for name, expr in spots.items():
        free = _df_free_gb(cfg, expr)
        if free is None:
            return "UNKNOWN", f"서버의 {name} 여유를 조회하지 못했다"
        msgs.append(f"{name} {free:.0f}GB")
        low |= free < need
    msg = f"여유 {', '.join(msgs)} / 필요 {need:.1f}GB(= 2 x (업로드 {ex['upload']} + 출력 {ex['output']} + 모델 {ex['model_download']}))"
    return ("LOW", msg + ". 서버 디스크가 부족해 실행하지 않는다") if low else ("OK", msg)


def check_sandbox(cfg: dict) -> None:
    bad = sandbox_violations(cfg)
    if bad:
        raise PipelineError("서버에서 쓸 수 있는 곳은 ~/dabin 아래뿐이다(사용자 결정). 바깥 경로: " + "; ".join(bad)
                            + ". configs의 server.sandbox_root, output.server_out_dir, dataset.upload_root를 ~/dabin/ 아래로 고친다")


def server_out_dir(cfg: dict) -> str:
    """서버 스크립트에 --out_dir로 넘기는 값. 원격: output.server_out_dir(서버 쪽). 로컬: server_out_dir가 있으면 그 경로(설정 파일 기준 절대 경로로 풀어),
    없으면 adapter_dir(이전 동작). 서버 스크립트는 어댑터를 <out_dir>/adapter/에 저장하므로 로컬에서 쓰려면 server_out_dir를 둔다(ADR 0032)."""
    o = cfg["output"].get("server_out_dir")
    if remote(cfg):
        return o
    return str(_p(cfg, o)) if o else str(adapter_dir(cfg))


def copy_local_adapter(cfg: dict) -> None:
    """로컬 실행: <server_out_dir>/adapter/를 adapter_dir로 복사한다(원격의 rsync에 해당). 같으면 아무것도 하지 않는다."""
    if not cfg["output"].get("server_out_dir"):
        return
    src, dst = Path(server_out_dir(cfg)) / "adapter", adapter_dir(cfg)
    if not src.is_dir():
        raise PipelineError(f"서버 스크립트가 어댑터를 {src}에 만들지 않았다")
    shutil.copytree(src, dst, dirs_exist_ok=True)


def fetch_script(cfg: dict, dest: Path) -> Path:
    """학습 서버의 정본 스크립트를 읽기만 해 dest에 저장한다. 설정의 script_sha256과 같을 때만 저장한다(다르면 아무것도 쓰지 않는다)."""
    if not remote(cfg):
        raise PipelineError("fetch-train-script는 server.ssh가 있는 설정(서버에서 가져올 설정)에 쓴다")
    role, name = cfg["role"], cfg["server"]["scripts"][cfg["role"]]
    st, why = script_hash_state(cfg)
    if st != "OK":
        raise PipelineError(f"저장하지 않았다. {why}")
    data = script_bytes(cfg)
    dest.mkdir(parents=True, exist_ok=True)
    tmp = dest / (name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest / name)      # 왜 임시 파일: 중간에 끊겨도 반쪽 스크립트가 정본 이름으로 남지 않는다
    return dest / name


def server_out_abs(cfg: dict) -> str:
    """서버 스크립트는 상대 --out_dir을 자기 작업 폴더(code_dir) 기준으로 쓴다(configs 주석). rsync는 홈 기준이므로 code_dir을 앞에 붙인다."""
    o = server_out_dir(cfg)
    return o if o.startswith(("/", "~")) else _srv(cfg, "code_dir").rstrip("/") + "/" + o


def script_bytes(cfg: dict) -> bytes | None:
    """서버 스크립트의 바이트. 읽지 못하면 None(로컬: 파일 없음, 원격: 연결·파일 없음)."""
    if not remote(cfg):
        sp = script_path(cfg)
        return sp.read_bytes() if sp.is_file() else None
    try:
        r = ssh_run(cfg, f"cat {_rq(_srv(cfg, 'code_dir') + '/' + cfg['server']['scripts'][cfg['role']])}", binary=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def missing_datasets(cfg: dict) -> list[str]:
    if not remote(cfg):
        return [p.name for p in dataset_files(cfg).values() if not p.is_file()]
    d, pre = _srv(cfg, "data_dir"), cfg["dataset"]["prefix"]
    names = [f"{pre}_{s}.jsonl" for s in SPLITS]
    try:
        r = ssh_run(cfg, "for f in " + " ".join(_rq(d + "/" + n) for n in names) + '; do test -f "$f" || echo "$f"; done')
    except (OSError, subprocess.SubprocessError):
        return names
    return names if r.returncode != 0 else [Path(x).name for x in r.stdout.split("\n") if x.strip()]


def remote_gpu_state(cfg: dict) -> tuple[bool, str]:
    try:
        r = ssh_run(cfg, "nvidia-smi -L")
    except (OSError, subprocess.SubprocessError):
        return False, "ssh를 실행하지 못했다"
    if r.returncode == 255:
        return False, f"ssh {remote(cfg)} 연결 실패(서버가 꺼져 있거나 ~/.ssh/config·키를 확인한다)"
    if r.returncode == 0 and r.stdout.strip():
        return True, r.stdout.strip().splitlines()[0][:80]
    return False, f"서버에서 GPU를 찾지 못했다(nvidia-smi 종료 코드 {r.returncode})"


def build_command(cfg: dict) -> tuple[list[str], list[str]]:
    """(명령, 주의 목록). 명령은 셸을 거치지 않는 인자 목록이다."""
    s, am, notes, skipped = cfg["server"], cfg["server"].get("arg_map", {}), [], []
    std = {"base_model": cfg["base_model"]["id"], "dataset_dir": _srv(cfg, "data_dir"),
           "dataset_prefix": cfg["dataset"]["prefix"], "output_dir": server_out_dir(cfg)}
    # 원격은 code_dir로 cd한 뒤 스크립트 이름으로 부른다(sha256sum -c가 같은 상대 이름을 읽는다)
    cmd = [s.get("python", "python3"), s["scripts"][cfg["role"]] if remote(cfg) else str(script_path(cfg))]
    for name, val in {**std, **cfg.get("hyperparams", {})}.items():
        flag = am.get(name)
        if flag and isinstance(val, bool):
            if val:
                cmd.append(flag)           # 값 없는 플래그(store_true). false면 넘기지 않는다
        elif flag:
            cmd += [flag, _fmt(val)]
        else:
            skipped.append(f"{name}={_fmt(val)}")
    if skipped:
        notes.append("인자 이름이 없어 넘기지 않는다(서버 스크립트 기본값에 맡김, 서버 기본값과 같은지 확인해야 한다): " + ", ".join(skipped))
    cmd += list(s.get("extra_args", []))
    if not s.get("args_verified", False):
        notes.append("server.args_verified=false: 서버 스크립트의 인자 이름을 확인하지 못했다. 확인 뒤 arg_map을 채우고 true로 바꾼다")
    return cmd, notes


def remote_command(cfg: dict, cmd: list[str]) -> str:
    """원격에서 돌릴 한 줄. (1) 쓸 폴더를 ~/dabin 아래에 만들고 (2) 정본 스크립트를 거기로 복사(읽기만 하는 원본은 그대로 둔다) (3) 사본의 해시를 확인하고 (4) 사본이 있는 폴더에서 실행한다.
    왜 사본에서 실행하나: 스크립트가 상대 경로에 무엇을 쓰더라도 ~/dabin 안에 머문다. 해시는 사본 기준이라 복사와 실행 사이에 바뀐 파일도 막는다.
    환경 변수는 TMPDIR, MPLCONFIGDIR(스크립트가 matplotlib을 가져온다, train_lora_a.py 154행)만 ~/dabin으로 돌린다. 모델 캐시(HF_HOME 등)는 건드리지 않는다.
    sha256은 check_ready가 설정에 있음을 이미 보장한다."""
    name = cfg["server"]["scripts"][cfg["role"]]
    sha = cfg["server"]["script_sha256"][cfg["role"]]
    sp = sandbox_paths(cfg)
    code, tmp, mpl = _rq(sp["code(스크립트 사본)"]), _rq(sp["TMPDIR"]), _rq(sp["MPLCONFIGDIR"])
    src = _rq(_srv(cfg, "code_dir").rstrip("/") + "/" + name)
    return (f"mkdir -p {code} {tmp} {mpl} && cp -f -- {src} {code}/{name} && cd {code} && echo '{sha}  {name}' | sha256sum -c --quiet - "
            f"&& exec env TMPDIR={tmp} MPLCONFIGDIR={mpl} {_rjoin(cmd)}")


def plan(cfg: dict) -> dict:
    cmd, notes = build_command(cfg)
    if remote(cfg):
        sha = cfg["server"].get("script_sha256", {}).get(cfg["role"])
        if not sha:
            notes.append("server.script_sha256가 없어 실행 명령(해시 확인 포함)을 만들 수 없다: train-doctor가 채우는 방법을 안내한다")
        rc = remote_command(cfg, cmd) if sha else _rjoin(cmd)
        if sandbox_violations(cfg):
            notes.append("~/dabin 밖의 쓰기 경로: " + "; ".join(sandbox_violations(cfg)))
        full = ["ssh", *SSH_OPTS, *SSH_LONG_OPTS, remote(cfg), rc]
        return {"role": cfg["role"], "command": full, "command_line": shlex.join(full), "cwd": f"{remote(cfg)}:{sandbox_paths(cfg)['code(스크립트 사본)']}",
                "remote": remote(cfg), "write_paths": sandbox_paths(cfg), "script_source": f"{_srv(cfg, 'code_dir')}/{cfg['server']['scripts'][cfg['role']]}(읽기만)", "server_out_dir": server_out_dir(cfg),
                "expected_adapter_dir": str(adapter_dir(cfg)), "fetch_from": f"{remote(cfg)}:{server_out_abs(cfg)}/adapter/",
                "expected_files": ["adapter_model.safetensors", "adapter_config.json"],
                "record_file": str(record_path(cfg)), "notes": notes, "config_hash": cfg["_config_hash"]}
    return {"role": cfg["role"], "command": cmd, "command_line": shlex.join(cmd), "cwd": str(_p(cfg, cfg["server"]["code_dir"])),
            "expected_adapter_dir": str(adapter_dir(cfg)),
            "expected_files": ["adapter_model.safetensors", "adapter_config.json"],
            "record_file": str(record_path(cfg)), "notes": notes, "config_hash": cfg["_config_hash"]}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def gpu_state() -> tuple[bool, str]:
    exe = shutil.which("nvidia-smi")
    if exe:
        try:
            r = subprocess.run([exe, "-L"], capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and r.stdout.strip():
                return True, r.stdout.strip().splitlines()[0][:80]
        except (OSError, subprocess.SubprocessError):
            pass
    if importlib.util.find_spec("torch"):
        try:
            import torch
            if torch.cuda.is_available():
                return True, torch.cuda.get_device_name(0)
        except Exception:   # noqa: BLE001
            pass
    return False, "GPU를 찾지 못했다(nvidia-smi와 torch.cuda 모두)"


# min_free_gb 기본 30: 근거 기록 없음(측정 안 함). 이전 비교 스크립트에서 가져온 값으로 보이나 출처 커밋을 찾지 못했다. 설정으로 조정한다.
def disk_free_gb(cfg: dict) -> tuple[float, Path]:
    """출력 폴더가 놓일 파일 시스템의 여유(GB)와 기준 경로. 시험에서 바꿔 끼울 수 있게 함수로 둔다."""
    probe = adapter_dir(cfg)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free / 1e9, probe


FILL_HOW = ("채우는 방법: 서버 스크립트를 읽어 arg_map·동작을 확인한 뒤(`ssh {host} cat {path}`), 해시를 구해 configs의 "
            "server.script_sha256.{role}에 적는다(`ssh {host} sha256sum {path}`, 출력의 앞 64자). 해시를 적는다는 것은 그 코드를 확인했다는 서명이다")


def script_hash_state(cfg: dict) -> tuple[str, str]:
    """(상태, 설명). 상태: OK | UNSET | MISMATCH | UNREADABLE. 원격이면 ssh로 스크립트를 읽어 비교한다."""
    role, want = cfg["role"], cfg["server"].get("script_sha256", {}).get(cfg["role"])
    name = cfg["server"]["scripts"][role]
    data = script_bytes(cfg)
    spath = _srv(cfg, "code_dir") + "/" + name
    how = FILL_HOW.format(host=remote(cfg) or "<서버>", path=spath, role=role)
    if data is None:
        return "UNREADABLE", f"{spath}를 읽을 수 없어 해시를 비교하지 못했다" + (f"(ssh {remote(cfg)})" if remote(cfg) else "")
    got = hashlib.sha256(data).hexdigest()
    if not want:
        return "UNSET", f"server.script_sha256.{role}가 비어 있다. 현재 {name}의 sha256은 {got}. {how}"
    if got != want:
        return "MISMATCH", (f"{name}의 sha256({got[:12]}...)이 설정({want[:12]}...)과 다르다. 서버 코드가 바뀌었다: "
                            "인자 이름(arg_map)을 다시 확인하고 script_sha256을 갱신한다")
    return "OK", f"{name} sha256 일치"


def declared_args(script: Path | bytes) -> set[str] | None:
    """스크립트가 add_argument로 선언한 `--이름`을 코드를 실행하지 않고 읽는다(ast). 읽거나 해석하지 못하면 None.
    왜 실행하지 않나: 서버 스크립트를 import하면 torch 적재와 부작용이 따른다. 정적으로 읽으면 GPU 없는 곳에서도 점검된다."""
    import ast
    try:
        tree = ast.parse(script.decode("utf-8") if isinstance(script, bytes) else script.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    names = {a.value for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "add_argument"
             for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.startswith("--")}
    return names or None


def arg_compat(cfg: dict) -> tuple[str, str]:
    """(상태, 설명). 상태: OK | MISMATCH | UNKNOWN. arg_map이 넘기는 인자(와 extra_args의 --이름)가 스크립트 선언에 있는지 본다."""
    s = cfg["server"]
    data = script_bytes(cfg)
    declared = declared_args(data) if data is not None else None
    if declared is None:
        return "UNKNOWN", "스크립트에서 add_argument 선언을 읽지 못해 인자 호환을 확인하지 못했다"
    used = {v for v in s.get("arg_map", {}).values() if v} | {x for x in s.get("extra_args", []) if isinstance(x, str) and x.startswith("--")}
    miss = sorted(used - declared)
    if miss:
        return "MISMATCH", f"스크립트가 선언하지 않은 인자: {', '.join(miss)}. server.arg_map을 스크립트의 add_argument에 맞춘다"
    return "OK", f"넘기는 인자 {len(used)}개가 모두 스크립트에 선언되어 있다"


def check_ready(cfg: dict) -> None:
    check_sandbox(cfg)      # 서버에 아무것도 쓰기 전에 가장 먼저
    where = f"ssh {remote(cfg)}의 " if remote(cfg) else ""
    if script_bytes(cfg) is None:
        raise PipelineError(f"서버 학습 스크립트를 읽을 수 없다: {where}{_srv(cfg, 'code_dir')}/{cfg['server']['scripts'][cfg['role']]}. server.code_dir·server.ssh를 확인한다(`train-doctor`)")
    miss = missing_datasets(cfg)
    if miss:
        raise PipelineError(f"데이터셋 파일이 없다: {miss}. dataset.dir/prefix를 확인한다")
    # 왜 비어 있어도 거부하나(G06): 해시가 없으면 어떤 코드를 돌리는지 확인하지 않은 채 학습하게 된다. 정본 코드가 바뀌면 같은 설정이 다른 어댑터를 낸다
    st, why = script_hash_state(cfg)
    if st != "OK":
        raise PipelineError(why)
    ast_, awhy = arg_compat(cfg)
    if ast_ == "MISMATCH":
        raise PipelineError(awhy)
    if remote(cfg) and not cfg["output"].get("server_out_dir"):
        raise PipelineError("server.ssh를 쓰려면 output.server_out_dir(서버 쪽 출력 폴더)가 필요하다")
    policy = cfg["output"].get("disk_policy", "block")
    if policy == "block":
        free, probe = disk_free_gb(cfg)
        need = cfg["output"].get("min_free_gb", 30)
        if free < need:
            raise PipelineError(f"디스크 여유가 부족하다: {free:.0f}GB < 필요 {need}GB(기준 {probe}). 정리하거나 output.min_free_gb를 조정한다. "
                                "WSL처럼 df가 호스트 여유를 보여 주지 않는 곳은 output.disk_policy를 warn으로 둔다(docs/runbook.md)")
    if cfg["server"].get("require_gpu", True) and not (remote_gpu_state(cfg) if remote(cfg) else gpu_state())[0]:
        raise PipelineError("[구멍: GPU 필요] GPU를 찾지 못했다. GPU가 있는 학습 서버에서(또는 server.ssh로) 실행한다(docs/runbook.md). 미리 보려면 --dry-run")
    if cfg["server"].get("require_gpu", True):
        st, why = gpu_busy_state(cfg)       # 실행 직전 검사. 비어 있지 않거나 확인하지 못하면 실행하지 않는다
        if st != "FREE":
            raise PipelineError(why)
    if remote(cfg):
        st, why = disk_state(cfg)
        if st != "OK":
            raise PipelineError(why)


def verify_output(cfg: dict, check_adapter) -> dict:
    """학습 결과가 package-adapters 입력 규약과 맞는지 확인한다(파일 두 개, 기반 모델, 순위). adapter_config 내용을 돌려준다.
    check_adapter는 adapter_check 컴포넌트의 함수를 wiring이 주입한다."""
    return check_adapter(cfg["role"], adapter_dir(cfg), {"id": cfg["base_model"]["id"]}, cfg["output"].get("max_lora_rank"))


def run_train(cfg: dict, *, check_adapter, log=print) -> dict:
    check_ready(cfg)
    pl = plan(cfg)
    log("실행: " + pl["command_line"])
    r = subprocess.run(pl["command"], cwd=None if remote(cfg) else pl["cwd"])
    if r.returncode == 255 and remote(cfg):
        raise PipelineError(f"ssh {remote(cfg)} 연결에 실패했거나 끊겼다(종료 코드 255). 서버에서 학습이 이어졌는지 확인한다: 학습은 서버에서 계속될 수 있다")
    if r.returncode != 0:
        raise PipelineError(f"서버 학습 스크립트가 실패했다(종료 코드 {r.returncode}). 위 출력을 확인한다"
                            + (" (해시 확인 실패라면 서버 코드가 바뀐 것이다)" if remote(cfg) else ""))
    if cfg.get("hyperparams", {}).get("dry_run"):
        # 왜: 서버 스크립트의 --dry_run은 학습 없이 점검만 하므로 어댑터가 없다. 어댑터 확인·기록을 하면 성공한 점검이 실패로 보인다(어댑터 없음).
        log("서버 dry_run 완료(종료 코드 0). 학습하지 않았으므로 어댑터 확인과 기록은 건너뛴다")
        return {"dry_run": True, "role": cfg["role"], "command": pl["command"], "exit_code": r.returncode}
    if remote(cfg):
        fetch_adapter(cfg, pl)
    else:
        copy_local_adapter(cfg)
    conf = verify_output(cfg, check_adapter)
    rec = write_record(cfg, pl, conf, r.returncode)
    log(f"완료: 어댑터 {pl['expected_adapter_dir']}, 기록 {pl['record_file']}")
    log(next_step(cfg))
    return rec


def fetch_adapter(cfg: dict, pl: dict) -> None:
    """서버의 <out_dir>/adapter/를 adapter_dir로 읽기 전용 복사한다(서버 쪽은 수정하지 않는다). 서버 스크립트의 저장 위치 규칙은 train_lora_a.py:143(`OUT / "adapter"`).
    이후 단계(package-adapters)는 이 로컬 폴더를 입력으로 받는다."""
    dst = adapter_dir(cfg)
    dst.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["rsync", "-a", "-e", shlex.join(["ssh", *SSH_OPTS]), pl["fetch_from"], str(dst) + "/"])
    if r.returncode != 0:
        raise PipelineError(f"서버의 어댑터를 가져오지 못했다(rsync 종료 코드 {r.returncode}): {pl['fetch_from']}. 서버에 어댑터가 있으면 같은 rsync를 손으로 다시 실행한다")


def _remote_dataset_hashes(cfg: dict) -> dict:
    d, pre = _srv(cfg, "data_dir"), cfg["dataset"]["prefix"]
    r = ssh_run(cfg, "sha256sum " + " ".join(_rq(f"{d}/{pre}_{s}.jsonl") for s in SPLITS))
    if r.returncode != 0:
        raise PipelineError("서버 데이터셋 해시를 구하지 못했다")
    return {Path(line.split(None, 1)[1].strip()).name: line.split()[0] for line in r.stdout.splitlines() if line.strip()}


def write_record(cfg: dict, pl: dict, conf: dict, rc: int) -> dict:
    a = adapter_dir(cfg)
    rec = {"record_version": 1, "role": cfg["role"], "config_hash": cfg["_config_hash"], "command": pl["command"], "exit_code": rc,
           # 원격: 실행 명령 안의 sha256sum -c가 이 값과 같음을 서버에서 확인하고 실행했다
           "server_script": (f"{remote(cfg)}:{_srv(cfg, 'code_dir')}/{cfg['server']['scripts'][cfg['role']]}" if remote(cfg) else str(script_path(cfg))),
           "server_script_sha256": (cfg["server"]["script_sha256"][cfg["role"]] if remote(cfg) else _sha256(script_path(cfg))),
           "base_model": {"id": cfg["base_model"]["id"], "requested_revision": cfg["base_model"].get("revision", "unspecified"),
                          "base_model_revision": None},
           "dataset": {"id": cfg["dataset"]["id"], "version": cfg["dataset"]["version"],
                       "content_hash": cfg["dataset"].get("content_hash"),
                       "file_sha256": (_remote_dataset_hashes(cfg) if remote(cfg) else
                                           {f"{cfg['dataset']['prefix']}_{s}.jsonl": _sha256(p) for s, p in dataset_files(cfg).items()})},
           "adapter": {"dir": str(a), "r": conf.get("r"), "files": {f: _sha256(a / f) for f in ("adapter_model.safetensors", "adapter_config.json")}},
           "hyperparams": cfg.get("hyperparams", {}), "notes": pl["notes"]}
    src = cfg["output"].get("server_run_record")
    if src and _p(cfg, src).is_file():   # 서버 run 기록의 키 이름은 확인하지 못했다. 있으면 아래 두 키만 옮긴다.
        try:
            sr = json.loads(_p(cfg, src).read_text(encoding="utf-8"))
            rec["base_model"]["base_model_revision"] = sr.get("base_model_revision")
            rec["dataset"]["content_hash"] = rec["dataset"]["content_hash"] or (sr.get("dataset") or {}).get("content_hash")
        except (OSError, ValueError, AttributeError):
            pass
    rp = record_path(cfg)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(rec, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return rec


def next_step(cfg: dict) -> str:
    other = "LORA_B" if cfg["role"] == "LORA_A" else "LORA_A"
    mine = f"--adapter-{cfg['role'][-1].lower()} {adapter_dir(cfg)}"
    ds = cfg["dataset"]
    return ("다음: A와 B 어댑터가 모두 있으면 docs/runbook.md의 `package-adapters` 절 명령을 쓴다. 이 역할 몫: "
            f"{mine} --dataset-id {ds['id']} --dataset-version {ds['version']} --dataset-hash {ds.get('content_hash') or '<sha256:... 서버 run 기록의 dataset>'} "
            f"--created-at <UTC 시각> 또는 환경 변수 SOURCE_DATE_EPOCH가 필요하다 "
            f"(다른 역할 {other}은 같은 방식으로 학습)")


def doctor(cfg: dict, *, profiles_dir: Path, cached_snapshot=None) -> list[tuple[str, str, str]]:
    """cached_snapshot은 hf_cache_lookup의 함수를 wiring이 주입한다(없으면 캐시 확인을 건너뛴다)."""
    """(상태, 항목, 안내). 상태는 OK | WARN | FAIL | HOLE."""
    out, s = [], cfg["server"]
    sp = script_path(cfg)
    spath = f"{_srv(cfg, 'code_dir')}/{s['scripts'][cfg['role']]}"
    if remote(cfg):
        out.append(("OK", "서버 접속", f"ssh {remote(cfg)}"))
        if script_bytes(cfg) is None:
            out[-1] = ("FAIL", "서버 접속", f"ssh {remote(cfg)}에서 {spath}를 읽지 못했다. 서버가 켜져 있는지, ~/.ssh/config의 호스트 이름, server.code_dir을 확인한다")
    out.append(("OK", "서버 스크립트", spath) if script_bytes(cfg) is not None else
               ("FAIL", "서버 스크립트", f"{spath} 없음. 학습 서버에서 실행하거나 configs/train.yaml의 server.code_dir·server.ssh를 고친다"))
    if remote(cfg):
        bad = sandbox_violations(cfg)
        out.append(("FAIL", "서버 쓰기 경로", "~/dabin 밖: " + "; ".join(bad) + ". 서버에서 쓸 수 있는 곳은 ~/dabin 아래뿐이다(사용자 결정)") if bad else
                   ("OK", "서버 쓰기 경로", "모두 ~/dabin 아래: " + ", ".join(sandbox_paths(cfg).values()) + " (모델 캐시만 서버 기본 위치를 읽기·새 파일 추가로 쓴다)"))
    miss = missing_datasets(cfg)
    out.append(("OK", "데이터셋", f"{cfg['dataset']['prefix']}_{{train,val,test}}.jsonl 있음") if not miss else
               ("FAIL", "데이터셋", f"없음: {miss} (dataset.dir={_srv(cfg, 'data_dir')}{' @' + remote(cfg) if remote(cfg) else ''})"))
    ok, why = remote_gpu_state(cfg) if remote(cfg) else gpu_state()
    if ok:
        out.append(("OK", "GPU", why))
    elif remote(cfg):
        out.append(("FAIL", "GPU", why))    # 원격은 서버를 직접 물었는데 없다는 답이므로 구멍이 아니라 실패다
    else:
        out.append(("HOLE", "GPU", "구멍: GPU 필요. " + why + ". 학습은 GPU가 있는 학습 서버에서 실행한다. 이 PC에서는 --dry-run까지만"))
    if s.get("args_verified", False) and any(s.get("arg_map", {}).values()):
        out.append(("OK", "서버 인자 이름", "확인됨(args_verified=true). 근거: " + (s.get("args_evidence") or "(server.args_evidence 없음: 근거를 적는다)")))
    else:
        out.append(("HOLE", "서버 인자 이름", "구멍: 서버 스크립트가 받는 인자 이름을 확인하지 못했다. 학습 서버에서 "
                    f"`{sp.name}`의 add_argument(또는 --help)로 확인해 server.arg_map을 채우고 args_verified를 true로, 근거를 args_evidence에 적는다"))
    if ok and s.get("require_gpu", True):
        gst, gwhy = gpu_busy_state(cfg)
        out.append(("OK" if gst == "FREE" else "FAIL" if gst == "BUSY" else "WARN", "GPU 점유", gwhy))
    if remote(cfg):
        dst, dwhy = disk_state(cfg)
        out.append(("OK" if dst == "OK" else "FAIL" if dst == "LOW" else "FAIL", "서버 디스크", dwhy))
    mods = s.get("python_modules", ["torch", "transformers", "peft", "trl", "datasets", "accelerate"])
    py = s.get("python", "python3")
    code = "import importlib.util as u,sys;print(' '.join(m for m in sys.argv[1:] if u.find_spec(m) is None))"
    try:
        r = (ssh_run(cfg, _rjoin([py, "-c", code, *mods])) if remote(cfg) else subprocess.run([py, "-c", code, *mods], capture_output=True, text=True, timeout=60))
        if r.returncode != 0:
            out.append(("FAIL", "파이썬 의존", f"{py}를 실행하지 못했다. server.python을 고친다"))
        elif r.stdout.strip():
            out.append(("FAIL", "파이썬 의존", f"{py}에 없음: {r.stdout.strip()}. 서버의 학습 환경(가상환경)을 server.python으로 지정한다"))
        else:
            out.append(("OK", "파이썬 의존", f"{', '.join(mods)} 있음"))
    except (OSError, subprocess.SubprocessError):
        out.append(("FAIL", "파이썬 의존", f"{py}를 실행하지 못했다. server.python을 고친다"))
    if remote(cfg):
        out.append(("HOLE", "기반 모델 캐시", "구멍: 서버의 모델 캐시는 이 PC에서 조회하지 않는다. 서버 스크립트가 첫 실행에 내려받는다"))
    else:
        try:   # 캐시 조회는 huggingface_hub 공개 API로 한다(T24)
            snap = cached_snapshot(cfg["base_model"]["id"], cfg["base_model"].get("revision"))
            out.append(("OK", "기반 모델 캐시", str(snap)) if snap else
                       ("WARN", "기반 모델 캐시", f"{cfg['base_model']['id']} 캐시가 없다. 첫 실행 때 내려받는다(오프라인이면 미리 받아 둔다)"))
        except ImportError:
            out.append(("WARN", "기반 모델 캐시", "huggingface_hub가 없어 캐시를 확인하지 못했다"))
    prof = cfg["base_model"].get("profile")
    if not prof:
        out.append(("WARN", "프로파일", "base_model.profile이 없다. package-adapters에 필요한 프로파일 이름을 적는다"))
    elif not (profiles_dir / f"{prof}.yaml").is_file():
        out.append(("WARN", "프로파일", f"profiles/{prof}.yaml이 없다. package-adapters 전에 만들어야 한다"))
    else:
        out.append(("OK", "프로파일", prof))
    policy = cfg["output"].get("disk_policy", "block")
    if policy != "off":
        free, probe = disk_free_gb(cfg)
        need = cfg["output"].get("min_free_gb", 30)
        bad = "FAIL" if policy == "block" else "WARN"
        out.append(("OK" if free >= need else bad, "디스크", f"{free:.0f}GB 여유(필요 {need}GB, 기준 {probe}, 정책 {policy}). WSL이면 호스트 여유와 다를 수 있다"))
    st, why = script_hash_state(cfg)
    out.append({"OK": ("OK", "서버 코드 해시", why), "UNSET": ("FAIL", "서버 코드 해시", why),
                "MISMATCH": ("FAIL", "서버 코드 해시", why), "UNREADABLE": ("FAIL", "서버 코드 해시", why)}[st])
    ast_, awhy = arg_compat(cfg)
    out.append({"OK": ("OK", "서버 인자 호환", awhy), "MISMATCH": ("FAIL", "서버 인자 호환", awhy), "UNKNOWN": ("WARN", "서버 인자 호환", awhy)}[ast_])
    return out


def format_doctor(rows) -> str:
    return "\n".join(f"[{st:4}] {name}: {msg}" for st, name, msg in rows)
