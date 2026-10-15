"""명령줄: model-lab run | run-once | status. 모든 동작은 설정 파일 하나가 정한다."""
from __future__ import annotations

import argparse
import json
import sys

from boundary_contracts import BoundaryError

from .config import load_config
from .errors import PipelineError
from .pipeline import STAGES, Run, run_once


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="model-lab")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="파이프라인을 실행한다(끝난 단계는 건너뛰고 이어서)")
    r.add_argument("--config", required=True)
    r.add_argument("--stop-after", choices=STAGES)
    o = sub.add_parser("run-once", help="최신 데이터셋 버전으로 한 번 실행한다. 이미 끝났으면 아무것도 하지 않는다(멱등)")
    o.add_argument("--config", required=True)
    s = sub.add_parser("status", help="실행 기록을 보여 준다")
    s.add_argument("--config", required=True)
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config)
        if a.cmd == "run":
            run = Run(cfg)
            rec = run.run(stop_after=a.stop_after)
            print(json.dumps({"run_id": run.run_id, "result": rec["result"]}, ensure_ascii=False))
        elif a.cmd == "run-once":
            print(json.dumps(run_once(cfg), ensure_ascii=False))
        else:
            run = Run(cfg)
            print(json.dumps({"run_id": run.run_id, "stages": {k: v.get("status") if isinstance(v, dict) else v
                                                                for k, v in run.rec["stages"].items()},
                              "result": run.rec["result"]}, ensure_ascii=False, indent=2))
        return 0
    except BoundaryError as e:
        print(f"경계 오류 {e.code} (retryable={e.retryable}): {e.message}", file=sys.stderr)
        return 3 if e.retryable else 2
    except PipelineError as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
