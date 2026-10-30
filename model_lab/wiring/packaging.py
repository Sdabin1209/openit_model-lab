"""패키지 만들기 연결: package_build에 model_registry의 사전 점검(시스템 프롬프트 파일, ModelPackageLayout)을 주입한다(wiring).
왜 주입: 두 컴포넌트는 서로를 import하지 않는다(규칙 3). 게시 때 거절될 디렉터리를 만들기 전에 알리려는 점검이다."""
from __future__ import annotations

from ..components import model_registry
from ..components.package_build import (SYSTEM_PROMPT_FILE, action_rules_version, build_manifest as _build, eval_report_ref,  # noqa: F401
                                        install_system_prompt, safe_id, training_question_sets)


def _precheck(adapter_dir, role, system_prompt) -> None:
    model_registry.verify_system_prompt(adapter_dir, system_prompt)
    model_registry.layout_of(adapter_dir, role)


def build_manifest(**kw) -> dict:
    return _build(precheck=_precheck, **kw)
