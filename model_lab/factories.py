"""설정의 팩토리 명세로 이웃 영역의 읽기 SDK·원격 레지스트리를 만든다(HTTP 클라이언트로 바꿀 수 있는 구조, D49).

명세: {"factory": "pkg.module:callable", "args": {...}}. args의 `<이름>_env: 변수` 항목은 환경 변수 값으로 바꿔
`<이름>`으로 넘긴다(비밀 값은 설정 파일에 두지 않고 진입점이 환경에서 읽는다, CONVENTIONS 7절)."""
from __future__ import annotations

import importlib
import os
from typing import Mapping

from .errors import ConfigError, PipelineError

HTTP_CLIENTS = {
    "model_registry": "boundary_contracts.clients:HttpModelRegistry",
    "dataset_store": "boundary_contracts.clients:HttpDatasetStore",
    "question_reader": "boundary_contracts.clients:HttpQuestionReader",
    "failure_store": "boundary_contracts.clients:HttpFailureStore",
}


def resolve_args(args: Mapping, env: Mapping = os.environ) -> dict:
    out = {}
    for k, v in args.items():
        if k.endswith("_env") and isinstance(v, str):
            if v not in env:
                raise ConfigError(f"환경 변수 {v}가 없다")
            out[k[:-4]] = env[v]
        else:
            out[k] = v
    return out


def load_callable(path: str):
    mod, fn = path.split(":")
    try:
        return getattr(importlib.import_module(mod), fn)
    except (ImportError, AttributeError) as e:
        raise PipelineError(f"{path}를 불러올 수 없다({type(e).__name__}). "
                            "boundary-contracts의 HTTP 클라이언트(v1.1.1 이상)가 설치되어 있어야 한다") from None


def build(spec: Mapping, *, default_factory: str | None = None, env: Mapping = os.environ):
    factory = spec.get("factory") or default_factory
    return load_callable(factory)(**resolve_args(spec.get("args", {}), env))


def build_http(kind: str, spec: Mapping, env: Mapping = os.environ):
    """{kind: http, base_url, token_env, timeout}"""
    args = {k: v for k, v in spec.items() if k not in ("kind", "factory")}
    return load_callable(HTTP_CLIENTS[kind])(**resolve_args(args, env))
