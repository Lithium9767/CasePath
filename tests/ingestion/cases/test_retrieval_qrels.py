from __future__ import annotations

import json
from pathlib import Path

from casepath.contracts import CaseIndexRecord
from casepath.ingestion.cases.ids import EXPECTED_LEXREL_CASE_COUNT
from casepath.ingestion.laws.jsonl import read_jsonl

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CASES_PATH = REPOSITORY_ROOT / "data/canonical/cases/cases_l1.jsonl"
QRELS_PATH = REPOSITORY_ROOT / "data/evaluation/case_retrieval_qrels.jsonl"


def test_relation_qrels_resolve_to_published_lexrel_cases():
    records = read_jsonl(CASES_PATH, CaseIndexRecord)
    by_id = {item.case_id: item for item in records}
    qrels = [json.loads(line) for line in QRELS_PATH.read_text(encoding="utf-8").splitlines()]
    assert len(qrels) == 13
    assert len({item["query_id"] for item in qrels}) == len(qrels)
    relevant_ids: set[str] = set()
    for item in qrels:
        ids = item["relevant_case_ids"]
        assert ids == sorted(set(ids))
        assert ids
        for case_id in ids:
            assert case_id in by_id
            assert item["relation_type"] in by_id[case_id].keywords
            assert "LexRel" in by_id[case_id].keywords
        relevant_ids.update(ids)
    assert len(relevant_ids) == EXPECTED_LEXREL_CASE_COUNT
