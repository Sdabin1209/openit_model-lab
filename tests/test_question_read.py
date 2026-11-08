"""I1 문항 정의 읽기(D13): 평가를 문항 유형·단위·범위별로 쪼갠다. 이웃 없이 메모리 대역으로 시험한다(CLAUDE.md 컨테이너와 개발)."""
import copy
import json
from pathlib import Path

import pytest
from boundary_contracts import BoundaryError

from model_lab.components import link_question_read
from tests.conftest import FIXED
from tests.doubles.fixtures import qid


def _bank(set_id, version):
    def q(n, data_type, unit, bounded):
        slot = {"slot_id": "s", "data_type": data_type, "units": [{"name": unit}] if unit else []}
        if bounded:
            slot["minimum"] = 0
        return {"question_id": qid(n), "slots": [slot]}
    return {"question_set_id": set_id, "version": int(version),
            "questions": [q(1, "number", "mg", True), q(2, "option", None, False), q(3, "text", None, False), q(4, "number", "kg", False)]}


class MemoryQuestionReader:
    """I1 QuestionReader 의 메모리 대역(읽기만). 어떤 (id, version) 이든 같은 모양의 문항 은행을 낸다."""
    def __init__(self, fail=None):
        self.fail, self.calls = fail, []

    def list_versions(self, question_set_id, *, cursor=None, limit=100):
        self.calls.append(("list_versions", question_set_id))
        return [], None

    def get_question_bank(self, ref):
        self.calls.append(("get_question_bank", ref["id"]))
        if self.fail:
            raise BoundaryError(self.fail, "x")
        return _bank(ref["id"], ref["version"])


def test_describe_reads_type_unit_and_range_from_the_question_definition():
    d = link_question_read.describe(_bank("qs", "1"))
    assert d[qid(1)] == {"question_type": ["number"], "unit": ["mg"], "range": "bounded"}
    assert d[qid(2)] == {"question_type": ["option"], "unit": [], "range": "unbounded"}


def test_catalog_uses_the_hash_in_the_reference_and_reads_each_set_once():
    r = MemoryQuestionReader()
    cat = link_question_read.catalog_for(r, [{"id": "a", "version": "1", "content_hash": "sha256:" + "0" * 64}, {"id": "a", "version": "1"}])
    assert list(cat) == [("a", "1")] and r.calls == [("get_question_bank", "a")]


def test_a_missing_set_version_is_reported_not_invented():
    with pytest.raises(KeyError):
        link_question_read.catalog_for(MemoryQuestionReader(), [{"id": "a", "version": "9"}])


def test_run_slices_the_evaluation_by_question_type_unit_and_range(cfg):
    from model_lab.wiring.pipeline import Run
    reader = MemoryQuestionReader()
    # 데이터셋의 문항 세트 참조에는 해시가 없으므로 I1 list_versions 에서 같은 버전의 참조를 찾는 경로를 탄다
    reader.list_versions = lambda i, **k: ([{"id": i, "version": "1", "content_hash": "sha256:" + "0" * 64}], None)
    Run(cfg, clock=FIXED, log=lambda s: None, question_reader=reader).run()
    rep = json.loads(next(Path(cfg["run"]["runs_dir"]).rglob("report-LORA_A.json")).read_text())
    assert {"question_type", "unit", "range"} <= set(rep["slices"])
    assert set(rep["slices"]["range"]) <= {"bounded", "unbounded"} and rep["slices"]["range"]


def test_run_continues_without_the_slices_when_the_reader_is_down(cfg):
    from model_lab.wiring.pipeline import Run
    c = copy.deepcopy(cfg); c["run"]["seed"] = 91
    rec = Run(c, clock=FIXED, log=lambda s: None, question_reader=MemoryQuestionReader(fail="UNAVAILABLE")).run()
    assert rec["result"]["status"] == "complete"
    rep = json.loads(next(Path(c["run"]["runs_dir"]).rglob("report-LORA_A.json")).read_text())
    assert "question_type" not in rep["slices"]
