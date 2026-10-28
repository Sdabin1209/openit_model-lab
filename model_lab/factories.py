"""설정의 팩토리 명세로 이웃 영역의 읽기 SDK·원격 레지스트리를 만든다(HTTP 클라이언트로 바꿀 수 있는 구조, D49).

명세: {"factory": "pkg.module:callable", "args": {...}}. args의 `<이름>_env: 변수` 항목은 `boundary_contracts.read_secret(변수)`로 읽어
`<이름>`으로 넘긴다(`변수_FILE` 경로의 파일이 우선, 없으면 `변수` 환경 변수, D84. 비밀 값은 설정 파일에 두지 않는다)."""
from __future__ import annotations

import importlib
import os
from typing import Mapping

from boundary_contracts import SecretError, read_secret

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
            try:   # NAME_FILE(파일 경로)이 있으면 그 내용, 없으면 NAME 환경 변수(D84). 값은 오류에 담기지 않는다
                out[k[:-4]] = read_secret(v, environ=env, required=True)
            except SecretError as e:
                raise ConfigError(str(e)) from None
        else:
            out[k] = v
    return out


# 왜(점검 C6, ADR 0017): 설정의 `module:callable`은 import 가능한 아무 호출 대상을 실행하게 한다. 설정 작성자는 운영자(신뢰 대상)이지만
# 오타·복사 실수·잘못된 설정 파일이 임의 호출이 되지 않도록 이름을 허용 목록으로 제한한다. 이름을 늘리려면 이 목록과 ADR을 고친다.
ALLOWED_FACTORIES = frozenset({
    *HTTP_CLIENTS.values(),
    "model_lab.file_dataset_store:file_dataset_store",      # 로컬 개발용 I2 읽기
    "tests.doubles.dataset_store:from_dir",                # 시험·CPU 스모크용 가짜 데이터(운영 이미지에는 tests 패키지가 없다)
})


def load_callable(path: str):
    if path not in ALLOWED_FACTORIES:
        raise ConfigError(f"허용 목록에 없는 팩토리다: {path}. 허용: {', '.join(sorted(ALLOWED_FACTORIES))}")
    mod, fn = path.split(":")
    try:
        return getattr(importlib.import_module(mod), fn)
    except (ImportError, AttributeError) as e:
        raise PipelineError(f"{path}를 불러올 수 없다({type(e).__name__}). "
                            "boundary-contracts의 HTTP 클라이언트가 설치되어 있어야 한다(I2 HttpDatasetStore는 v2.1.0 이상)") from None


def build(spec: Mapping, *, default_factory: str | None = None, env: Mapping = os.environ):
    factory = spec.get("factory") or default_factory
    return load_callable(factory)(**resolve_args(spec.get("args", {}), env))


def build_http(kind: str, spec: Mapping, env: Mapping = os.environ):
    """{kind: http, base_url, token_env, timeout}"""
    args = {k: v for k, v in spec.items() if k not in ("kind", "factory")}
    return load_callable(HTTP_CLIENTS[kind])(**resolve_args(args, env))
