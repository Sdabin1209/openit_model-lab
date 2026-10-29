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
from boundary_contracts import canonical, validator

from .errors import ConfigError

ROLES = ("LORA_A", "LORA_B")
ROLE_TO_FILE_ROLE = {"LORA_A": "LORA_A", "LORA_B": "LORA_B"}      # DatasetManifest.files[].role (2.0.0, D74)
CONFIG_VERSION = 1

_ID = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]*$", "maxLength": 128}
_NUM = {"type": "number"}
_QUANT = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "mode": {"enum": ["none", "4bit-nf4", "8bit"]},
        "compute_dtype": {"enum": ["float16", "bfloat16", "float32"]},
    },
}
_LORA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "r": {"type": "integer", "minimum": 1}, "alpha": {"type": "integer", "minimum": 1},
        "dropout": {"type": "number", "minimum": 0, "maximum": 1},
        "target_modules": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    },
}
_TRAINING = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "trainer": {"enum": ["hf", "dry-run"]},
        "quantization": _QUANT,
        "lora": _LORA,
        "max_seq_len": {"type": "integer", "minimum": 16},
        "per_device_batch_size": {"type": "integer", "minimum": 1},
        "grad_accum_steps": {"type": "integer", "minimum": 1},
        "learning_rate": {"type": "number", "exclusiveMinimum": 0},
        "warmup_ratio": {"type": "number", "minimum": 0, "maximum": 1},
        "epochs": {"type": "number", "exclusiveMinimum": 0},
        "max_steps": {"type": ["integer", "null"], "minimum": 1},
        "max_train_samples": {"type": ["integer", "null"], "minimum": 1},
        "gpu_count": {"type": "integer", "minimum": 1},
        "gpu_memory_fraction": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
        "gradient_checkpointing": {"type": "boolean"},
        "checkpoint_every_steps": {"type": "integer", "minimum": 1},
        "log_every_steps": {"type": "integer", "minimum": 1},
        "deterministic": {"type": "boolean"},
        "attn_implementation": {"enum": ["eager", "sdpa", "flash_attention_2"]},
        "dtype": {"enum": ["float16", "bfloat16", "float32"]},
    },
}
_BASE_MODEL = {
    "type": "object", "additionalProperties": False, "required": ["id"],
    "properties": {"id": {"type": "string", "minLength": 1}, "revision": {"type": "string", "minLength": 1}},
}
_BACKEND = {
    "type": "object", "additionalProperties": False, "required": ["kind"],
    "properties": {
        "kind": {"enum": ["hf", "fake-oracle"]},
        # fake-oracle: 평가 경로 시험용. N번째마다 깨진 출력을 낸다(보호 경로 확인). 0이면 끈다.
        "corrupt_every": {"type": "integer", "minimum": 0},
    },
}
_FACTORY = {"type": "object", "additionalProperties": False, "required": ["factory"],
            "properties": {"factory": {"type": "string", "pattern": r"^[\w.]+:\w+$"}, "args": {"type": "object"}}}
# 왜: 영역 사이 호출은 HTTP다(D49). I2도 D100으로 HTTP가 되어 registry와 같은 {kind: http, base_url(_env), token_env, timeout} 모양을 쓴다.
_HTTP_STORE = {"type": "object", "additionalProperties": False, "required": ["kind"],
               "properties": {"kind": {"const": "http"}, "base_url": {"type": "string"}, "base_url_env": {"type": "string"},
                              "token_env": {"type": "string"}, "timeout": {"type": "number", "exclusiveMinimum": 0}}}
_SERVING = {"type": "object"}  # 형식은 validate_config가 계약 ServingConfig 스키마로 검증한다(model-runtime 사본을 쓰지 않는다, U42)

CONFIG_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object", "additionalProperties": False,
    "required": ["config_version", "run", "inputs", "roles", "serving", "evaluation", "registry"],
    "properties": {
        "config_version": {"const": CONFIG_VERSION},
        "profile": {"type": "string"},
        "run": {
            "type": "object", "additionalProperties": False, "required": ["seed"],
            "properties": {"name": {"type": "string"}, "seed": {"type": "integer", "minimum": 0, "maximum": 2**31 - 1},
                           "runs_dir": {"type": "string"}},
        },
        "inputs": {
            "type": "object", "additionalProperties": False, "required": ["schema_dir", "dataset", "dataset_store"],
            "properties": {
                "schema_dir": {"type": "string"},
                "dataset": {"type": "object", "additionalProperties": False, "required": ["id"],
                            "properties": {"id": _ID, "version": {"type": "string", "minLength": 1}}},
                # 이웃(synthetic-data)의 읽기 SDK를 만드는 함수. 'pkg.module:callable', args는 키워드 인자.
                "dataset_store": {"oneOf": [_FACTORY, _HTTP_STORE]},
                # 아직 쓰지 않는 이웃 읽기 SDK. 같은 방식으로 고른다(HttpQuestionReader, HttpFailureStore 등).
                "question_reader": _FACTORY, "failure_store": _FACTORY,
            },
        },
        "base_model": _BASE_MODEL,
        "training": _TRAINING,
        "roles": {
            "type": "object", "additionalProperties": False, "minProperties": 1,
            "properties": {r: {
                "type": "object", "additionalProperties": False, "required": ["package_id", "system_prompt_file"],
                # system_prompt_file: 학습에 쓴 시스템 프롬프트 파일(설정 파일 기준 상대 경로). 패키징이 어댑터 디렉터리에 넣는다(D75).
                "properties": {"package_id": _ID, "system_prompt_file": {"type": "string", "minLength": 1},
                               "training": _TRAINING}} for r in ROLES},
        },
        "serving": {"type": "object", "additionalProperties": False, "properties": {r: _SERVING for r in ROLES}},
        "evaluation": {
            "type": "object", "additionalProperties": False, "required": ["backend"],
            "properties": {
                "backend": _BACKEND,
                "split": {"enum": ["EVAL", "VALIDATION"]},
                "limit_samples": {"type": ["integer", "null"], "minimum": 1},
                "max_listed_failures": {"type": "integer", "minimum": 0},
                # 회귀 평가(I7). 켜면 inputs.failure_store에서 확정된 실패 사례를 읽는다. label을 바꾸면 새 run이 된다
                # (실패 사례 저장소의 내용은 run_id에 들어가지 않으므로 집합이 달라졌음을 사람이 표시한다).
                "regression": {"type": "object", "additionalProperties": False,
                               "properties": {"enabled": {"type": "boolean"}, "limit_cases": {"type": ["integer", "null"], "minimum": 1},
                                              "label": {"type": "string"}}},
            },
        },
        # 게시 대상: file(로컬 파일), http(원격 model-registry; boundary_contracts.clients.HttpModelRegistry),
        # factory(임의 구현). 토큰은 token_env가 가리키는 환경 변수에서 읽는다.
        "registry": {
            "type": "object", "additionalProperties": False, "required": ["kind"],
            "properties": {"kind": {"enum": ["file", "http", "factory"]}, "path": {"type": "string"},
                           "base_url": {"type": "string"}, "base_url_env": {"type": "string"}, "token_env": {"type": "string"},
                           "timeout": {"type": "number", "exclusiveMinimum": 0},
                           "factory": {"type": "string", "pattern": r"^[\w.]+:\w+$"}, "args": {"type": "object"}},
        },
        "checks": {"type": "object", "additionalProperties": False,
                   "properties": {"max_lora_rank": {"type": "integer", "minimum": 1}}},
        # 계약 기록의 turn_engine_version 표지(D109). 코드 의존이 아니므로 기본은 packager.DEFAULT_TURN_ENGINE_MARK.
        "lineage": {"type": "object", "additionalProperties": False,
                    "properties": {"turn_engine_version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+$"}}},
        "publish": {"type": "object", "additionalProperties": False,
                    "properties": {"enabled": {"type": "boolean"}}},
    },
}
_VALIDATOR = jsonschema.Draft202012Validator(CONFIG_SCHEMA)


def deep_merge(base: dict, over: dict) -> dict:
    """over가 base를 덮어쓴다. 객체는 재귀, 그 밖(목록 포함)은 통째로 바꾼다."""
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


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


_SECRET_KEY_PARTS = ("token", "secret", "password", "credential", "api_key", "authorization", "private_key")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]+$")


def check_inline_secrets(value, path: str = "config") -> None:
    """비밀 값이 설정에 직접 적혀 있으면 거부한다(D84). 키 이름에 token/secret/password/credential/api_key/authorization/
    private_key가 들어 있고 값이 문자열이면 거부한다. `*_env`는 환경 변수 *이름*을 받으므로 이름 형식([A-Z][A-Z0-9_]+)만 허용한다.
    오류에는 키 경로와 안내만 싣고 값은 싣지 않는다."""
    if isinstance(value, dict):
        for k, v in value.items():
            low, p = str(k).lower(), f"{path}.{k}"
            if any(s in low for s in _SECRET_KEY_PARTS) and isinstance(v, str):
                if low.endswith("_env"):
                    if not _ENV_NAME.match(v):
                        raise ConfigError(f"설정 {p}: 환경 변수 이름 형식이 아니다([A-Z][A-Z0-9_]+). 값을 직접 적지 않는다. 비밀은 NAME_FILE로 준다")
                else:
                    raise ConfigError(f"설정 {p}: 비밀 값을 설정 파일에 직접 적을 수 없다. 비밀은 NAME_FILE로 주고 설정에는 `<키>_env: NAME`만 쓴다")
            check_inline_secrets(v, p)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            check_inline_secrets(v, f"{path}[{i}]")


def validate_config(cfg: dict) -> None:
    check_inline_secrets(cfg)
    errs = sorted(_VALIDATOR.iter_errors(cfg), key=lambda e: list(map(str, e.absolute_path)))
    if errs:
        msg = "; ".join(f"/{'/'.join(map(str, e.absolute_path))}: {e.message[:120]}" for e in errs[:8])
        raise ConfigError(f"설정 형식 위반: {msg}")
    # 왜 여기서 한 번: 서빙 설정의 형식은 계약 ServingConfig가 정한다(모르는 키 거부). 여기서 보증한 값을 패키저·파이프라인이 다시 검사하지 않는다(R7).
    for role, sc in (cfg.get("serving") or {}).items():
        serr = sorted(validator("ServingConfig").iter_errors(sc), key=lambda e: list(map(str, e.absolute_path)))
        if serr:
            raise ConfigError(f"설정 형식 위반: /serving/{role}/{'/'.join(map(str, serr[0].absolute_path))}: {serr[0].message[:120]}")


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


def resolved_path(cfg: dict, value: str) -> Path:
    # 왜: 문진 스키마는 태그로 설치한 패키지(requirements.lock의 openit-survey-schema)에서 읽는다. 이웃 폴더나 사본 경로를 설정에 박지 않기 위한 접두어.
    if value == "installed:survey-schema":
        import survey_schema
        return Path(survey_schema.contract_version_dir(1)) / "schemas"
    p = Path(value)
    return p if p.is_absolute() else (Path(cfg["_base_dir"]) / p).resolve()


def system_prompt_text(cfg: dict, role: str) -> str:
    """역할의 시스템 프롬프트(UTF-8 파일 내용 그대로, 줄바꿈을 고치지 않는다)."""
    p = resolved_path(cfg, cfg["roles"][role]["system_prompt_file"])
    try:
        text = p.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        raise ConfigError(f"{role}: system_prompt_file을 읽을 수 없다: {p.name}") from None
    if not text.strip():
        raise ConfigError(f"{role}: system_prompt_file이 비어 있다: {p.name}")
    return text


def role_training(cfg: dict, role: str) -> dict:
    """역할에 적용되는 학습 설정(공통 training 위에 역할별 training)."""
    return deep_merge(cfg.get("training", {}), cfg["roles"][role].get("training", {}))


def config_hash(cfg: dict) -> str:
    """실행을 식별하는 설정 해시. 경로 해석에 쓰는 _base_dir과 runs_dir는 제외한다(위치가 달라도 같은 설정)."""
    c = {k: v for k, v in cfg.items() if k != "_base_dir"}
    c["run"] = {k: v for k, v in c["run"].items() if k not in ("runs_dir", "name")}
    c["registry"] = {"kind": c["registry"]["kind"]}      # 게시 위치는 결과(해시)에 영향이 없다
    c["inputs"] = {k: v for k, v in c["inputs"].items() if k not in ("schema_dir",)}
    return canonical.sha256_hash(c)
