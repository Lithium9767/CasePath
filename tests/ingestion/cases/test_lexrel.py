import json
from pathlib import Path

from casepath.ingestion.cases.ids import (
    EXPECTED_LEXREL_RETRIEVAL_CASE_COUNT,
    EXPECTED_LEXREL_SERVICE_CASE_COUNT,
    LEXREL_CASE_ID_PREFIX,
)
from casepath.ingestion.cases.lexrel import load_retrieval_cases, load_service_contract_cases
from casepath.ingestion.cases.sections import numbered_fact_items
from casepath.rule_layer.ids import RULE_SERVICE_TERMINATION_REFUND

LEXREL_INPUT = Path(__file__).resolve().parents[3] / "data/inputs/lexrel_service_contract.json"
LEXREL_RETRIEVAL_INPUT = (
    Path(__file__).resolve().parents[3] / "data/inputs/lexrel_retrieval_cases.json"
)


def test_numbered_fact_items_keep_original_prefixes():
    text = "1. 双方签订服务合同。\n2. 被告未支付费用；\n"
    assert numbered_fact_items(text) == [
        "1. 双方签订服务合同。",
        "2. 被告未支付费用；",
    ]


def test_lexrel_service_contract_snapshot_converts_to_case_records():
    lightweight, deep = load_service_contract_cases(LEXREL_INPUT)
    assert len(lightweight) == EXPECTED_LEXREL_SERVICE_CASE_COUNT
    assert len(deep) == EXPECTED_LEXREL_SERVICE_CASE_COUNT
    by_id = {record.case_id: record for record in deep}
    for index, record in zip(lightweight, deep, strict=True):
        assert index.case_id == record.case_id
        assert index.case_id.startswith(LEXREL_CASE_ID_PREFIX)
        assert index.facts_text
        assert record.findings
        assert all(finding.predicate in index.facts_text for finding in record.findings)
        assert record.decisions == []
        assert record.reasoning_steps == []
    ctrip = by_id[f"{LEXREL_CASE_ID_PREFIX}829d6968-3b55-43c8-9e18-9d6ccccfd391"]
    assert "退还" in ctrip.claims[0].requested_remedy
    dental = by_id[f"{LEXREL_CASE_ID_PREFIX}55d87466-4e50-4e16-bfd3-7ff06aaeaad6"]
    assert "原数据未标注具体诉请" in dental.claims[0].requested_remedy
    assert dental.condition_findings == []
    gym_like = by_id[f"{LEXREL_CASE_ID_PREFIX}102f10d1-ca09-4ed1-ae2e-f45007ce3038"]
    # LexRel relation participants do not encode litigation roles.
    assert gym_like.claims[0].claimant is None
    assert gym_like.claims[0].respondent is None
    assert gym_like.claims[0].invoked_rule_ids == [RULE_SERVICE_TERMINATION_REFUND]


def test_balanced_retrieval_subset_is_unique_and_keeps_relation_labels():
    lightweight, deep = load_retrieval_cases(LEXREL_RETRIEVAL_INPUT)
    assert len(lightweight) == EXPECTED_LEXREL_RETRIEVAL_CASE_COUNT
    assert {item.case_id for item in lightweight} == {item.case_id for item in deep}
    assert len({"".join(item.facts_text.split()) for item in lightweight}) == len(lightweight)
    source = json.loads(LEXREL_RETRIEVAL_INPUT.read_text(encoding="utf-8"))
    primary_counts: dict[str, int] = {}
    by_id = {item.case_id: item for item in lightweight}
    for selected in source:
        relation = selected["primary_relation_type"]
        primary_counts[relation] = primary_counts.get(relation, 0) + 1
        index = by_id[f"{LEXREL_CASE_ID_PREFIX}{selected['sample']['uniqid']}"]
        assert set(selected["sample"]["relation"]).issubset(index.keywords)
        assert index.keywords[-2:] == ["LexRel", "民事案例"]
    assert len(primary_counts) == 12
    assert set(primary_counts.values()) == {8}
