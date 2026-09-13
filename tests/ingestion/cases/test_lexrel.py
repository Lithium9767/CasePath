from pathlib import Path

from casepath.ingestion.cases.ids import (
    EXPECTED_LEXREL_SERVICE_CASE_COUNT,
    LEXREL_CASE_ID_PREFIX,
)
from casepath.ingestion.cases.lexrel import UNANNOTATED_REMEDY, load_service_contract_cases
from casepath.ingestion.cases.sections import numbered_fact_items
from casepath.rule_layer.ids import RULE_SERVICE_TERMINATION_REFUND

LEXREL_INPUT = Path(__file__).resolve().parents[3] / "data/inputs/lexrel_service_contract.json"


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
        assert record.title == index.title == f"LexRel服务合同关系案例 {index.case_id[7:]}"
        assert all(claim.claimant is None and claim.respondent is None for claim in record.claims)
    ctrip = by_id[f"{LEXREL_CASE_ID_PREFIX}829d6968-3b55-43c8-9e18-9d6ccccfd391"]
    assert ctrip.claims[0].requested_remedy == "要求退团、退款；"
    assert ctrip.claims[0].amount is None
    dental = by_id[f"{LEXREL_CASE_ID_PREFIX}55d87466-4e50-4e16-bfd3-7ff06aaeaad6"]
    assert dental.claims[0].requested_remedy == UNANNOTATED_REMEDY
    assert dental.condition_findings == []
    gym_like = by_id[f"{LEXREL_CASE_ID_PREFIX}102f10d1-ca09-4ed1-ae2e-f45007ce3038"]
    assert gym_like.claims[0].requested_remedy == UNANNOTATED_REMEDY
    assert gym_like.claims[0].amount is None
    assert gym_like.claims[0].invoked_rule_ids == []
    training = by_id[f"{LEXREL_CASE_ID_PREFIX}1ec928ab-bb1e-4131-bafc-e17593454e1b"]
    assert training.claims[0].requested_remedy == "主张退还1，200元的教育辅导培训费"
    assert training.claims[0].amount == 1200
    assert training.claims[0].invoked_rule_ids == [RULE_SERVICE_TERMINATION_REFUND]
