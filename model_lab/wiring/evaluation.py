"""평가 연결: 모델 호출(model_infer) -> 채점(eval_sample) -> 보고(eval_report)를 잇는다(wiring)."""
from __future__ import annotations

from typing import Mapping

from ..components import eval_report, eval_sample, model_infer


def evaluate_sample(role: str, session, sample: Mapping, *, flow: Mapping | None = None) -> dict:
    inf = model_infer.infer(session, {"role": role, "input": sample["input"]})
    return eval_sample.score({"role": role, "sample": dict(sample), "inference": inf, **({"flow": dict(flow)} if flow else {})})


def evaluate_set(role: str, session, rset: dict) -> list:
    return [(s, evaluate_sample(role, session, s)) for s in rset["samples"][role]]


def build_report(role: str, session, samples: list[dict], *, seen_sets: set, eval_files: list[dict], max_listed_failures: int = 20,
                 context: Mapping | None = None, return_records: bool = False, flow: Mapping | None = None):
    recs = [(s, evaluate_sample(role, session, s, flow=flow)) for s in samples]
    rep = eval_report.build_report(role, recs, seen_sets=seen_sets, eval_files=eval_files, max_listed_failures=max_listed_failures, context=context)
    return (rep, recs) if return_records else rep


def regression_section(role: str, session, rset: dict, *, max_listed: int = 20, recs: list | None = None) -> dict:
    recs = recs if recs is not None else evaluate_set(role, session, rset)
    return eval_report.regression_section(role, rset, recs, max_listed=max_listed)
