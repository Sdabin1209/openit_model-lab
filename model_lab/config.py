"""실행 설정: YAML/JSON 파일 하나 + 프로파일 병합 + 형식 검증.

설정에는 통과 임계치와 지표 선정을 두지 않는다(O11). 스키마가 알 수 없는 키를 거부하므로
`threshold` 같은 키는 넣을 수도 없다. 12GB 호스트에 맞춘 값은 profiles/gpu-dev-12g.yaml에만 있다.
"""
from __future__ import annotations

import copy
import re
import json
from pathlib import Path
from typing import Any

import jsonschema
import yaml
from boundary_contracts import canonical

from .wiring import schemas
from .wiring.errors import ConfigError
from .wiring.settings import CONFIG_VERSION, ROLES, ROLE_TO_FILE_ROLE, deep_merge  # noqa: F401


# 왜: PyYAML은 YAML 1.1이라 off·no·yes·on이 불리언으로, 0123이 8진수 83으로 읽히고 중복 키는 마지막 값이 조용히 이긴다(점검 C2).
# YAML 1.2.2 사양(10.3.2 코어 스키마)은 불리언을 true/false(대소문자 3형)로만, 정수는 10진 [-+]?[0-9]+ · 0o8진 · 0x16진으로 정하고
# 매핑 키는 유일해야 한다(3.2.1.1). 그래서 PyYAML의 SafeLoader를 코어 스키마로 좁히고 중복 키를 거부한다(ADR 0015).
# ruamel.yaml(1.2 기본)을 쓰지 않은 이유: 의존을 늘리지 않으려는 것이 아니라 설치·잠금·검증을 하지 않았기 때문이다.
class _Yaml12Loader(yaml.SafeLoader):
    pass


_CORE = {
    "tag:yaml.org,2002:null": (re.compile(r"^(?:~|null|Null|NULL|)$"), "~nN"),
    "tag:yaml.org,2002:bool": (re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), "tTfF"),
    "tag:yaml.org,2002:int": (re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$"), "-+0123456789"),
    "tag:yaml.org,2002:float": (re.compile(r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"),
                                "-+0123456789."),
}
_Yaml12Loader.yaml_implicit_resolvers = {}
for _tag, (_re, _first) in _CORE.items():
    _Yaml12Loader.add_implicit_resolver(_tag, _re, list(_first) + ([""] if _tag.endswith(":null") else []))   # ""는 빈 값


def _int(loader, node):
    v = loader.construct_scalar(node)
    sign = -1 if v.startswith("-") else 1
    v = v.lstrip("+-")
    return sign * (int(v[2:], 8) if v.startswith("0o") else int(v[2:], 16) if v.startswith("0x") else int(v, 10))


def _float(loader, node):
    v = loader.construct_scalar(node).lower()
    if v.endswith(".inf"):
        return float("-inf") if v.startswith("-") else float("inf")
    return float("nan") if v == ".nan" else float(v)


def _mapping(loader, node, deep=False):
    seen = set()
    for k, _ in node.value:
        key = loader.construct_object(k, deep=True)
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f"중복 키: {key!r}", k.start_mark)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_Yaml12Loader.add_constructor("tag:yaml.org,2002:int", _int)
_Yaml12Loader.add_constructor("tag:yaml.org,2002:float", _float)
_Yaml12Loader.construct_mapping = _mapping


def load_yaml12(text: str):
    return yaml.load(text, Loader=_Yaml12Loader)  # noqa: S506  SafeLoader 파생이라 임의 객체를 만들지 않는다


def _read(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text) if path.suffix == ".json" else load_yaml12(text)
    except (OSError, ValueError, yaml.YAMLError) as e:
        raise ConfigError(f"설정 파일을 읽을 수 없다: {path}: {type(e).__name__}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"설정 파일의 최상위는 객체여야 한다: {path}")
    return data


def _path(parts, root: str) -> str:
    out = root
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else f".{p}"
    return out


def check_inline_secrets(value, path: str = "config") -> None:
    """비밀 값이 설정에 직접 적혀 있으면 거부한다(D84). 규칙은 스키마 SecretFree가 정한다(키 이름과 `*_env` 이름 형식).
    오류에는 키 경로와 안내만 싣고 값은 싣지 않는다(스키마 오류 메시지는 값을 담으므로 쓰지 않는다)."""
    errs = schemas.errors("SecretFree", value)
    if errs:
        e = errs[0]
        p = _path(e.absolute_path, path)
        if str(e.validator) == "pattern":
            raise ConfigError(f"설정 {p}: 환경 변수 이름 형식이 아니다([A-Z][A-Z0-9_]+). 값을 직접 적지 않는다. 비밀은 NAME_FILE로 준다")
        raise ConfigError(f"설정 {p}: 비밀 값을 설정 파일에 직접 적을 수 없다. 비밀은 NAME_FILE로 주고 설정에는 `<키>_env: NAME`만 쓴다")


def validate_config(cfg: dict) -> None:
    """설정 형식은 스키마 RunConfig가 전부 정한다(서빙 설정은 계약 ServingConfig를 가리킨다, U42). 여기서 보증한 값을 컴포넌트가 다시 검사하지 않는다(R7)."""
    check_inline_secrets(cfg)
    errs = schemas.errors("RunConfig", cfg)
    if errs:
        msg = "; ".join(f"/{'/'.join(map(str, e.absolute_path))}: {e.message[:120]}" for e in errs[:8])
        raise ConfigError(f"설정 형식 위반: {msg}")


PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def load_profile(name: str) -> dict:
    """profiles/<name>.yaml. 이름은 파일 이름뿐이다(경로 불가)."""
    if not _NAME.match(name):
        raise ConfigError("프로파일 이름은 파일 이름이어야 한다(경로 불가)")
    p = PROFILES_DIR / f"{name}.yaml"
    if not p.is_file():
        raise ConfigError(f"프로파일이 없다: {name} (profiles/ 아래: {', '.join(sorted(x.stem for x in PROFILES_DIR.glob('*.yaml')))})")
    return _read(p)


STD_ARGS = ("base_model", "dataset_dir", "dataset_prefix", "output_dir")


def load_train_config(path: str | Path) -> dict:
    path = Path(path).resolve()
    cfg = _read(path)
    check_inline_secrets(cfg)
    errs = schemas.errors("TrainWrapConfig", cfg)
    if errs:
        raise ConfigError("학습 설정 형식 위반: " + "; ".join(f"/{'/'.join(map(str, e.absolute_path))}: {e.message[:120]}" for e in errs[:8]))
    # 스키마 밖 조건(schemas/README.md): 다른 속성의 값을 키 집합으로 쓰는 조건과 값 중복 금지는 JSON Schema로 쓸 수 없다
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
    cfg["_base_dir"] = str(path.parent)
    cfg["_config_hash"] = canonical.content_hash({k: v for k, v in cfg.items() if not k.startswith("_")})
    return cfg


def load_config(path: str | Path) -> dict:
    """설정을 읽고 프로파일을 병합한 최종 설정(dict)을 낸다. 상대 경로는 설정 파일 위치 기준이다.

    프로파일은 `profile:` 키가 가리키는 파일이고, 설정 파일의 값이 프로파일을 덮어쓴다."""
    path = Path(path).resolve()
    cfg = _read(path)
    prof_ref = cfg.get("profile")
    if prof_ref:
        prof_path = (path.parent / prof_ref).resolve()
        cfg = deep_merge(_read(prof_path), cfg)
    validate_config(cfg)
    cfg = copy.deepcopy(cfg)
    cfg["_base_dir"] = str(path.parent)
    return cfg


