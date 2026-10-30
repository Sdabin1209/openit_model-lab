"""계약 EvaluationReport 조립 컴포넌트: 지표 목록과 패키지·데이터셋 참조를 받아 EvaluationReport(계약)를 낸다.

입력 EvaluationReportRequest(평가함) 또는 UnevaluatedReportRequest(평가하지 않음, package-adapters), 출력은 둘 다 계약 EvaluationReport.
계약 지표 레지스트리에 없는 이름이 있으면 게시하지 않는다(ML-01)."""
from __future__ import annotations

from typing import Mapping

from boundary_contracts import PIPELINE_SCHEMA_VERSION, canonical, metric_name_problems, validator


def evaluation_report(*, role: str, report_id: str, created_at: str, package: Mapping, dataset: Mapping, question_sets: list,
                      backend: Mapping, decoding: Mapping, metrics: list, notes: list, detail: Mapping) -> dict:
    r = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "report_id": report_id, "created_at": created_at, "role": role,
         "package": dict(package), "dataset": dict(dataset), "question_sets": list(question_sets),
         "evaluation_backend": {"name": backend["name"], "version": backend["version"],
                                "constrained_decoding": bool(backend.get("constrained_decoding", False))},
         "decoding": {k: decoding[k] for k in ("temperature", "seed") if k in decoding},
         "metrics": metrics, "notes": notes, "ext": {"model-lab": {"detail": detail}}}
    bad = metric_name_problems(r)       # 왜: 계약 레지스트리에 없는 이름의 보고서는 게시하지 않는다(ML-01, 게이트가 이름으로 맞댄다)
    if bad:
        raise ValueError(f"metric names not in contract registry (METRICS.md): {bad[:5]}")
    r["content_hash"] = canonical.content_hash(r)
    validator("EvaluationReport").validate(r)
    return r


def unevaluated_report(*, role, package, dataset, created_at, decoding, report_id) -> dict:
    r = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "report_id": report_id, "created_at": created_at, "role": role,
         "package": package, "dataset": dataset, "question_sets": [],
         "evaluation_backend": {"name": "none", "version": "0", "constrained_decoding": False},
         "decoding": {k: decoding[k] for k in ("temperature", "seed") if k in decoding},
         "metrics": [{"name": "schema_valid_raw", "group": "PROTECTION", "value": 0.0, "n": 0, "higher_is_better": True, "unit": "ratio"}],   # 왜: 레지스트리에 '평가 안 함' 지표가 없어 n=0인 레지스트리 지표로 낸다(n=0은 계산하지 못함, METRICS.md). 요청: CR-L011
         "notes": ["평가하지 않았다: 학습 서버에서 만든 어댑터를 model-lab package-adapters로 패키징만 했다(n=0은 계산하지 못했다는 뜻)."],
         "ext": {"model-lab": {"attached": True}}}
    r["content_hash"] = canonical.content_hash(r)
    validator("EvaluationReport").validate(r)
    return r
