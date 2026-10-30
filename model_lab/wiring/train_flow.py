"""`model-lab train` 연결: server_train_wrap에 어댑터 점검(adapter_check)과 기반 모델 캐시 조회(hf_cache_lookup)를 주입한다(wiring)."""
from __future__ import annotations

from ..components import adapter_check, hf_cache_lookup
from ..components import server_train_wrap as _w
from ..components.server_train_wrap import (SPLITS, build_command, check_ready, dataset_files, format_doctor, next_step, plan,  # noqa: F401
                                            record_path, script_path)
from ..config import PROFILES_DIR, load_train_config  # noqa: F401


def run_train(cfg: dict, *, log=print) -> dict:
    return _w.run_train(cfg, check_adapter=adapter_check.check_adapter, log=log)


def doctor(cfg: dict) -> list[tuple[str, str, str]]:
    return _w.doctor(cfg, profiles_dir=PROFILES_DIR, cached_snapshot=hf_cache_lookup.cached_snapshot)
