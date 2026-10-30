"""명령줄: model-lab run | run-once | status | package-adapters | train | train-doctor. 모든 동작은 설정 파일 하나가 정한다."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from boundary_contracts import BoundaryError

from .config import load_config
from .wiring.errors import PipelineError
from .wiring.pipeline import STAGES, Run, run_once


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
    p = sub.add_parser("package-adapters", help="학습 서버가 만든 어댑터 두 개를 패키지로 만들어 게시한다(D84, 평가 없음)")
    p.add_argument("--config", required=True, help="서빙 설정·패키지 ID·게시 대상(registry)을 가진 설정 파일")
    p.add_argument("--profile", help="프로파일 이름(profiles/<이름>.yaml). 기반 모델을 정한다. 생략하면 설정의 profile")
    p.add_argument("--adapter-a", required=True); p.add_argument("--adapter-b", required=True)
    p.add_argument("--prompt-a", required=True); p.add_argument("--prompt-b", required=True)
    p.add_argument("--dataset-id", required=True); p.add_argument("--dataset-version", required=True)
    p.add_argument("--dataset-hash", required=True, help="어댑터를 학습한 데이터셋의 content_hash(학습 run 기록의 dataset)")
    p.add_argument("--train-record-a", help="`train`가 쓴 LORA_A 학습 기록(*.train-wrap.json). 계보를 대조해 패키지에 넣는다. 없으면 경고")
    p.add_argument("--train-record-b", help="LORA_B 학습 기록")
    p.add_argument("--training-run-id"); p.add_argument("--version"); p.add_argument("--base-revision"); p.add_argument("--created-at")
    p.add_argument("--out", required=True, help="패키지를 만들 디렉터리")
    p.add_argument("--no-publish", action="store_true", help="패키지만 만들고 게시하지 않는다")
    t = sub.add_parser("train", help="학습 서버의 정본 학습 스크립트를 감싸 실행한다(D78). --dry-run은 명령만 보여 준다")
    t.add_argument("--config", required=True, help="학습 설정(configs/train.yaml)")
    t.add_argument("--dry-run", action="store_true")
    d = sub.add_parser("train-doctor", help="학습을 돌릴 수 있는지 점검하고 해결 안내를 말한다")
    d.add_argument("--config", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd in ("train", "train-doctor"):
            from .wiring import train_flow as tw
            tcfg = tw.load_train_config(a.config)
            if a.cmd == "train-doctor":
                rows = tw.doctor(tcfg)
                print(tw.format_doctor(rows))
                return 1 if any(r[0] == "FAIL" for r in rows) else 0
            if a.dry_run:
                pl = tw.plan(tcfg)
                print(json.dumps(pl, ensure_ascii=False, indent=2))
                print("(--dry-run: 실행하지 않았다)")
                return 0
            tw.run_train(tcfg)
            return 0
        if a.cmd == "package-adapters":
            from .wiring.attach_flow import package_adapters
            res = package_adapters(
                config=a.config, profile=a.profile, out=Path(a.out), publish=not a.no_publish,
                adapters={"LORA_A": a.adapter_a, "LORA_B": a.adapter_b}, prompts={"LORA_A": a.prompt_a, "LORA_B": a.prompt_b},
                dataset={"id": a.dataset_id, "version": a.dataset_version, "content_hash": a.dataset_hash},
                train_records={"LORA_A": a.train_record_a, "LORA_B": a.train_record_b},
                training_run_id=a.training_run_id, version=a.version, base_revision=a.base_revision, created_at=a.created_at)
            for role, v in res.items():
                for w in v.get("warnings", []):
                    print(f"경고 {role}: {w}", file=sys.stderr)
            print(json.dumps(res, ensure_ascii=False))
            return 0
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
