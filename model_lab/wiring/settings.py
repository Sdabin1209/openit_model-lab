"""영역 설정에서 파생하는 값(경로 해석, 역할별 학습 설정, 설정 해시, 문진 스키마 정수 버전). 컴포넌트가 아니라 wiring의 몫이다(규칙 5)."""
from __future__ import annotations

import copy
from pathlib import Path

from boundary_contracts import canonical

from .errors import ConfigError, PipelineError


def contract_version(schema_dir: Path) -> int:
    p = Path(schema_dir).parent / "CONTRACT_VERSION"
    try:
        return int(p.read_text().strip())
    except (OSError, ValueError):
        raise PipelineError("schema_dir 상위의 CONTRACT_VERSION을 읽을 수 없다") from None


ROLES = ("LORA_A", "LORA_B")
ROLE_TO_FILE_ROLE = {"LORA_A": "LORA_A", "LORA_B": "LORA_B"}      # DatasetManifest.files[].role (2.0.0, D74)


def ref_of(manifest: dict) -> dict:
    return {"id": manifest["dataset_id"], "version": manifest["version"], "content_hash": manifest["content_hash"]}


def deep_merge(base: dict, over: dict) -> dict:
    """over가 base를 덮어쓴다. 객체는 재귀, 그 밖(목록 포함)은 통째로 바꾼다."""
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


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
