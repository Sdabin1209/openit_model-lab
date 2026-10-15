"""실행 설정: YAML/JSON 파일 하나 + 프로파일 병합 + 형식 검증.

설정에는 통과 임계치와 지표 선정을 두지 않는다(O11). 스키마가 알 수 없는 키를 거부하므로
`threshold` 같은 키는 넣을 수도 없다. 12GB 호스트에 맞춘 값은 profiles/gpu-dev-12g.yaml에만 있다.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema
import yaml
from boundary_contracts import canonical

from .errors import ConfigError

ROLES = ("LORA_A", "LORA_B")
ROLE_TO_FILE_ROLE = {"LORA_A": "PARSER", "LORA_B": "GENERATOR"}
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
_SERVING = {"type": "object"}  # 형식은 model-runtime의 validate_serving_config가 검증한다

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
                "dataset_store": {"type": "object", "additionalProperties": False, "required": ["factory"],
                                  "properties": {"factory": {"type": "string", "pattern": r"^[\w.]+:\w+$"},
                                                 "args": {"type": "object"}}},
            },
        },
        "base_model": _BASE_MODEL,
        "training": _TRAINING,
        "roles": {
            "type": "object", "additionalProperties": False, "minProperties": 1,
            "properties": {r: {
                "type": "object", "additionalProperties": False, "required": ["package_id"],
                "properties": {"package_id": _ID, "training": _TRAINING}} for r in ROLES},
        },
        "serving": {"type": "object", "additionalProperties": False, "properties": {r: _SERVING for r in ROLES}},
        "evaluation": {
            "type": "object", "additionalProperties": False, "required": ["backend"],
            "properties": {
                "backend": _BACKEND,
                "split": {"enum": ["EVAL", "VALIDATION"]},
                "limit_samples": {"type": ["integer", "null"], "minimum": 1},
                "max_listed_failures": {"type": "integer", "minimum": 0},
            },
        },
        "registry": {
            "type": "object", "additionalProperties": False, "required": ["kind"],
            "properties": {"kind": {"enum": ["file"]}, "path": {"type": "string"}},
        },
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


def _read(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    except (OSError, ValueError, yaml.YAMLError) as e:
        raise ConfigError(f"설정 파일을 읽을 수 없다: {path}: {type(e).__name__}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"설정 파일의 최상위는 객체여야 한다: {path}")
    return data


def validate_config(cfg: dict) -> None:
    errs = sorted(_VALIDATOR.iter_errors(cfg), key=lambda e: list(map(str, e.absolute_path)))
    if errs:
        msg = "; ".join(f"/{'/'.join(map(str, e.absolute_path))}: {e.message[:120]}" for e in errs[:8])
        raise ConfigError(f"설정 형식 위반: {msg}")


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
    p = Path(value)
    return p if p.is_absolute() else (Path(cfg["_base_dir"]) / p).resolve()


def role_training(cfg: dict, role: str) -> dict:
    """역할에 적용되는 학습 설정(공통 training 위에 역할별 training)."""
    return deep_merge(cfg.get("training", {}), cfg["roles"][role].get("training", {}))


def config_hash(cfg: dict) -> str:
    """실행을 식별하는 설정 해시. 경로 해석에 쓰는 _base_dir과 runs_dir는 제외한다(위치가 달라도 같은 설정)."""
    c = {k: v for k, v in cfg.items() if k != "_base_dir"}
    c["run"] = {k: v for k, v in c["run"].items() if k not in ("runs_dir", "name")}
    c["registry"] = {k: v for k, v in c["registry"].items() if k != "path"}
    c["inputs"] = {k: v for k, v in c["inputs"].items() if k not in ("schema_dir",)}
    return canonical.sha256_hash(c)
