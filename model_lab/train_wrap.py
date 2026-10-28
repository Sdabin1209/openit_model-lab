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
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import jsonschema
from boundary_contracts import canonical

from .attach import PROFILES_DIR, check_adapter
from .config import ROLES, _read, check_inline_secrets
from .errors import ConfigError, PipelineError

STD_ARGS = ("base_model", "dataset_dir", "dataset_prefix", "output_dir")
SPLITS = ("train", "val", "test")
_ID = r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
_FLAG = {"type": ["string", "null"], "pattern": r"^-{1,2}[A-Za-z][A-Za-z0-9_-]*$"}
_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["config_version", "role", "server", "base_model", "dataset", "output"],
    "properties": {
        "config_version": {"const": 1},
        "role": {"enum": list(ROLES)},
        "server": {
            "type": "object", "additionalProperties": False, "required": ["code_dir", "scripts"],
            "properties": {
                "code_dir": {"type": "string", "minLength": 1},
                "scripts": {"type": "object", "additionalProperties": False, "required": list(ROLES),
                            "properties": {r: {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9._-]*\.py$"} for r in ROLES}},
                "python": {"type": "string", "minLength": 1},
                "require_gpu": {"type": "boolean"},
                "args_verified": {"type": "boolean"},
                "args_evidence": {"type": "string"},
                # arg_map을 확인한 시점의 서버 스크립트 sha256(역할별). 서버 코드가 바뀌면 doctor와 학습 전 점검이 알린다(ML-12).
                "script_sha256": {"type": "object", "additionalProperties": False,
                                  "properties": {r: {"type": "string", "pattern": r"^[0-9a-f]{64}$"} for r in ROLES}},
                "arg_map": {"type": "object", "additionalProperties": _FLAG},
                "extra_args": {"type": "array", "items": {"type": "string"}},
                "python_modules": {"type": "array", "items": {"type": "string", "pattern": r"^[A-Za-z0-9_]+$"}},
            },
        },
        "base_model": {"type": "object", "additionalProperties": False, "required": ["id"],
                       "properties": {"id": {"type": "string", "pattern": _ID}, "revision": {"type": "string"},
                                      "profile": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9._-]*$"}}},
        "dataset": {"type": "object", "additionalProperties": False, "required": ["dir", "prefix", "id", "version"],
                    "properties": {"dir": {"type": "string", "minLength": 1}, "prefix": {"type": "string", "minLength": 1},
                                   "id": {"type": "string", "minLength": 1}, "version": {"type": "string", "minLength": 1},
                                   "content_hash": {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"}}},
        "hyperparams": {"type": "object", "propertyNames": {"pattern": r"^[a-z][a-z0-9_]*$"},
                        "additionalProperties": {"type": ["string", "number", "boolean"]}},
        "output": {"type": "object", "additionalProperties": False, "required": ["adapter_dir"],
                   "properties": {"adapter_dir": {"type": "string", "minLength": 1}, "record_file": {"type": "string"},
                                  "server_run_record": {"type": "string"}, "min_free_gb": {"type": "number", "minimum": 0},
                                  # 디스크 여유 부족 때 학습 전 점검과 doctor가 어떻게 하나(ML-2): block(기본, 멈춤) | warn | off.
                                  # WSL의 df는 호스트 여유를 보여 주지 않으므로(docs/runbook.md) 그런 호스트는 warn/off로 두고 예산은 따로 관리한다
                                  "disk_policy": {"enum": ["block", "warn", "off"]},
                                  "max_lora_rank": {"type": "integer", "minimum": 1}}},
    },
}
_VALIDATOR = jsonschema.Draft202012Validator(_SCHEMA)


def load_train_config(path: str | Path) -> dict:
    path = Path(path).resolve()
    cfg = _read(path)
    if not isinstance(cfg, dict):
        raise ConfigError("학습 설정의 최상위는 객체여야 한다")
    check_inline_secrets(cfg)
    errs = sorted(_VALIDATOR.iter_errors(cfg), key=lambda e: list(map(str, e.absolute_path)))
    if errs:
        raise ConfigError("학습 설정 형식 위반: " + "; ".join(f"/{'/'.join(map(str, e.absolute_path))}: {e.message[:120]}" for e in errs[:8]))
    am = cfg["server"].get("arg_map", {})
    known = set(STD_ARGS) | set(cfg.get("hyperparams", {}))
    extra = sorted(set(am) - known)
    if extra:
        raise ConfigError(f"server.arg_map에 쓰이지 않는 이름이 있다: {extra} (표준 {list(STD_ARGS)} 또는 hyperparams의 키만 가능)")
    flags = [f for f in am.values() if f]
    if len(flags) != len(set(flags)):
        raise ConfigError("server.arg_map에 같은 인자 이름이 두 번 쓰였다")
    prof = cfg["base_model"].get("profile")
    if prof and (PROFILES_DIR / f"{prof}.yaml").is_file():
        pid = (_read(PROFILES_DIR / f"{prof}.yaml").get("base_model") or {}).get("id")
        if pid != cfg["base_model"]["id"]:
            raise ConfigError(f"base_model.id({cfg['base_model']['id']})가 프로파일 {prof}의 기반 모델({pid})과 다르다")
    cfg = json.loads(json.dumps(cfg))
    base = path.parent
    cfg["_base_dir"] = str(base)
    cfg["_config_hash"] = canonical.content_hash({k: v for k, v in cfg.items() if not k.startswith("_")})
    return cfg


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


def build_command(cfg: dict) -> tuple[list[str], list[str]]:
    """(명령, 주의 목록). 명령은 셸을 거치지 않는 인자 목록이다."""
    s, am, notes, skipped = cfg["server"], cfg["server"].get("arg_map", {}), [], []
    std = {"base_model": cfg["base_model"]["id"], "dataset_dir": str(_p(cfg, cfg["dataset"]["dir"])),
           "dataset_prefix": cfg["dataset"]["prefix"], "output_dir": str(adapter_dir(cfg))}
    cmd = [s.get("python", "python3"), str(script_path(cfg))]
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


def plan(cfg: dict) -> dict:
    cmd, notes = build_command(cfg)
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


def script_hash_state(cfg: dict) -> tuple[str, str]:
    """(상태, 설명). 상태: OK | UNSET | MISMATCH | UNREADABLE. 서버 스크립트가 읽히는 환경에서만 비교한다(서버에 접속하지 않는다)."""
    want = cfg["server"].get("script_sha256", {}).get(cfg["role"])
    sp = script_path(cfg)
    if not sp.is_file():
        return "UNREADABLE", f"{sp}를 읽을 수 없어 해시를 비교하지 못했다"
    got = _sha256(sp)
    if not want:
        return "UNSET", f"server.script_sha256.{cfg['role']}가 없다. 현재 {sp.name}의 sha256은 {got}. arg_map을 확인한 코드라면 이 값을 적는다"
    if got != want:
        return "MISMATCH", (f"{sp.name}의 sha256({got[:12]}...)이 설정({want[:12]}...)과 다르다. 서버 코드가 바뀌었다: "
                            "인자 이름(arg_map)을 다시 확인하고 script_sha256을 갱신한다")
    return "OK", f"{sp.name} sha256 일치"


def check_ready(cfg: dict) -> None:
    sp = script_path(cfg)
    if not sp.is_file():
        raise PipelineError(f"서버 학습 스크립트가 없다: {sp}. server.code_dir을 확인한다(`train-doctor`)")
    miss = [str(p) for p in dataset_files(cfg).values() if not p.is_file()]
    if miss:
        raise PipelineError(f"데이터셋 파일이 없다: {miss}. dataset.dir/prefix를 확인한다")
    st, why = script_hash_state(cfg)
    if st == "MISMATCH":
        raise PipelineError(why)
    policy = cfg["output"].get("disk_policy", "block")
    if policy == "block":
        free, probe = disk_free_gb(cfg)
        need = cfg["output"].get("min_free_gb", 30)
        if free < need:
            raise PipelineError(f"디스크 여유가 부족하다: {free:.0f}GB < 필요 {need}GB(기준 {probe}). 정리하거나 output.min_free_gb를 조정한다. "
                                "WSL처럼 df가 호스트 여유를 보여 주지 않는 곳은 output.disk_policy를 warn으로 둔다(docs/runbook.md)")
    if cfg["server"].get("require_gpu", True) and not gpu_state()[0]:
        raise PipelineError("[구멍: GPU 필요] 이 PC에는 GPU가 없다. 학습 서버에서 실행한다(docs/runbook.md). 미리 보려면 --dry-run")


def verify_output(cfg: dict) -> dict:
    """학습 결과가 package-adapters 입력 규약과 맞는지 확인한다(파일 두 개, 기반 모델, 순위). adapter_config 내용을 돌려준다."""
    return check_adapter(cfg["role"], adapter_dir(cfg), {"id": cfg["base_model"]["id"]}, cfg["output"].get("max_lora_rank"))


def run_train(cfg: dict, *, log=print) -> dict:
    check_ready(cfg)
    pl = plan(cfg)
    log("실행: " + pl["command_line"])
    r = subprocess.run(pl["command"], cwd=pl["cwd"])
    if r.returncode != 0:
        raise PipelineError(f"서버 학습 스크립트가 실패했다(종료 코드 {r.returncode}). 위 출력을 확인한다")
    conf = verify_output(cfg)
    rec = write_record(cfg, pl, conf, r.returncode)
    log(f"완료: 어댑터 {pl['expected_adapter_dir']}, 기록 {pl['record_file']}")
    log(next_step(cfg))
    return rec


def write_record(cfg: dict, pl: dict, conf: dict, rc: int) -> dict:
    a = adapter_dir(cfg)
    rec = {"record_version": 1, "role": cfg["role"], "config_hash": cfg["_config_hash"], "command": pl["command"], "exit_code": rc,
           "server_script": str(script_path(cfg)), "server_script_sha256": _sha256(script_path(cfg)),
           "base_model": {"id": cfg["base_model"]["id"], "requested_revision": cfg["base_model"].get("revision", "unspecified"),
                          "base_model_revision": None},
           "dataset": {"id": cfg["dataset"]["id"], "version": cfg["dataset"]["version"],
                       "content_hash": cfg["dataset"].get("content_hash"),
                       "file_sha256": {f"{cfg['dataset']['prefix']}_{s}.jsonl": _sha256(p) for s, p in dataset_files(cfg).items()}},
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
            f"(다른 역할 {other}은 같은 방식으로 학습)")


def doctor(cfg: dict) -> list[tuple[str, str, str]]:
    """(상태, 항목, 안내). 상태는 OK | WARN | FAIL | HOLE."""
    out, s = [], cfg["server"]
    sp = script_path(cfg)
    out.append(("OK", "서버 스크립트", str(sp)) if sp.is_file() else
               ("FAIL", "서버 스크립트", f"{sp} 없음. 학습 서버에서 실행하거나 configs/train.yaml의 server.code_dir을 고친다"))
    miss = [p.name for p in dataset_files(cfg).values() if not p.is_file()]
    out.append(("OK", "데이터셋", f"{cfg['dataset']['prefix']}_{{train,val,test}}.jsonl 있음") if not miss else
               ("FAIL", "데이터셋", f"없음: {miss} (dataset.dir={_p(cfg, cfg['dataset']['dir'])})"))
    ok, why = gpu_state()
    if ok:
        out.append(("OK", "GPU", why))
    else:
        out.append(("HOLE", "GPU", "구멍: GPU 필요. " + why + ". 학습은 GPU가 있는 학습 서버에서 실행한다. 이 PC에서는 --dry-run까지만"))
    if s.get("args_verified", False) and any(s.get("arg_map", {}).values()):
        out.append(("OK", "서버 인자 이름", "확인됨(args_verified=true). 근거: " + (s.get("args_evidence") or "(server.args_evidence 없음: 근거를 적는다)")))
    else:
        out.append(("HOLE", "서버 인자 이름", "구멍: 서버 스크립트가 받는 인자 이름을 확인하지 못했다. 학습 서버에서 "
                    f"`{sp.name}`의 add_argument(또는 --help)로 확인해 server.arg_map을 채우고 args_verified를 true로, 근거를 args_evidence에 적는다"))
    mods = s.get("python_modules", ["torch", "transformers", "peft", "trl", "datasets", "accelerate"])
    py = s.get("python", "python3")
    code = "import importlib.util as u,sys;print(' '.join(m for m in sys.argv[1:] if u.find_spec(m) is None))"
    try:
        r = subprocess.run([py, "-c", code, *mods], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            out.append(("FAIL", "파이썬 의존", f"{py}를 실행하지 못했다. server.python을 고친다"))
        elif r.stdout.strip():
            out.append(("FAIL", "파이썬 의존", f"{py}에 없음: {r.stdout.strip()}. 서버의 학습 환경(가상환경)을 server.python으로 지정한다"))
        else:
            out.append(("OK", "파이썬 의존", f"{', '.join(mods)} 있음"))
    except (OSError, subprocess.SubprocessError):
        out.append(("FAIL", "파이썬 의존", f"{py}를 실행하지 못했다. server.python을 고친다"))
    try:   # 캐시 조회는 huggingface_hub 공개 API로 한다(T24)
        from .hf_cache import cached_snapshot
        snap = cached_snapshot(cfg["base_model"]["id"], cfg["base_model"].get("revision"))
        out.append(("OK", "기반 모델 캐시", str(snap)) if snap else
                   ("WARN", "기반 모델 캐시", f"{cfg['base_model']['id']} 캐시가 없다. 첫 실행 때 내려받는다(오프라인이면 미리 받아 둔다)"))
    except ImportError:
        out.append(("WARN", "기반 모델 캐시", "huggingface_hub가 없어 캐시를 확인하지 못했다"))
    prof = cfg["base_model"].get("profile")
    if not prof:
        out.append(("WARN", "프로파일", "base_model.profile이 없다. package-adapters에 필요한 프로파일 이름을 적는다"))
    elif not (PROFILES_DIR / f"{prof}.yaml").is_file():
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
    out.append({"OK": ("OK", "서버 코드 해시", why), "UNSET": ("WARN", "서버 코드 해시", why),
                "MISMATCH": ("FAIL", "서버 코드 해시", why), "UNREADABLE": ("WARN", "서버 코드 해시", why)}[st])
    return out


def format_doctor(rows) -> str:
    return "\n".join(f"[{st:4}] {name}: {msg}" for st, name, msg in rows)
