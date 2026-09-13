"""Relation annotations do not establish parties' actual procedural requests."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from casepath.ingestion.cases.ids import CLAIM_TYPE_SERVICE_TERMINATION_REFUND
from casepath.ingestion.cases.lexrel import (
    UNANNOTATED_REMEDY,
    convert_sample,
    load_service_contract_cases,
)
from casepath.rule_layer.ids import RULE_SERVICE_TERMINATION_REFUND

SOURCE = Path(__file__).resolve().parents[3] / "data/inputs/lexrel_service_contract.json"


def sample(facts):
    return {
        "uniqid": "test-procedural-roles",
        "input": facts,
        "relation": {
            "服务合同关系": [
                {
                    "主体": "甲、乙",
                    "客体": "提供健身服务",
                    "内容": "甲有权请求乙解除合同并退还费用9000元。",
                }
            ]
        },
    }


def test_relation_participant_order_does_not_invent_litigation_roles():
    source = sample("双方签订服务合同，甲已支付9000元服务费。")
    before = deepcopy(source)
    index, record = convert_sample(source)
    source["relation"]["服务合同关系"][0]["主体"] = "乙、甲"
    reversed_index, reversed_record = convert_sample(source)
    assert (index, record) == (reversed_index, reversed_record)
    assert index.title == "LexRel服务合同关系案例 test-procedural-roles"
    assert record.claims[0].claimant is record.claims[0].respondent is None
    assert record.claims[0].requested_remedy == UNANNOTATED_REMEDY
    assert record.claims[0].amount is None
    assert record.claims[0].invoked_rule_ids == []
    assert source["input"] == before["input"]


@pytest.mark.parametrize(
    "facts",
    [
        "双方签订了服务合同。",
        "原告已支付1200元服务费，门店一直没有开业。",
        "服务接受者有权请求提供者退还1200元服务费。",
        "协议约定双方解除合同时客户可以要求返还服务款。",
        "原告未要求退款。",
        "原告没有请求支付1000元。",
        "记录与原告所主张的事实一致。",
    ],
)
def test_missing_request_is_not_filled_from_contract_rights_or_annotations(facts):
    _, record = convert_sample(sample(facts))
    assert record.claims[0].requested_remedy == UNANNOTATED_REMEDY
    assert record.claims[0].amount is None
    assert record.claims[0].invoked_rule_ids == []


def test_explicit_request_uses_source_amount_not_annotation_amount():
    facts = "双方订立服务合同。原告请求判令：解除合同；返还未消费服务费1，250.25元。"
    index, record = convert_sample(sample(facts))
    claim = record.claims[0]
    assert claim.requested_remedy in facts
    assert claim.amount == 1250.25
    assert claim.claim_type == CLAIM_TYPE_SERVICE_TERMINATION_REFUND
    assert claim.invoked_rule_ids == [RULE_SERVICE_TERMINATION_REFUND]
    assert index.facts_text == facts


def test_pinned_overlays_keep_evidence_but_do_not_restore_invented_claims():
    samples = json.loads(SOURCE.read_text(encoding="utf-8"))
    source_by_id = {f"lexrel.{item['uniqid']}": item for item in samples}
    lightweight, deep = load_service_contract_cases(SOURCE)
    by_id = {item.case_id: item for item in lightweight}
    for record in deep:
        index = by_id[record.case_id]
        claim = record.claims[0]
        assert index.facts_text == source_by_id[record.case_id]["input"].strip()
        assert (
            claim.requested_remedy == UNANNOTATED_REMEDY
            or claim.requested_remedy in index.facts_text
        )
        assert record.maturity == "L1"
        assert not any(item.human_verified for item in record.condition_findings)
        assert record.decisions == record.reasoning_steps == []
        for span in record.source_spans:
            assert index.facts_text[span.start_offset : span.end_offset] == span.quote
    for suffix in (
        "102f10d1-ca09-4ed1-ae2e-f45007ce3038",
        "8a246ddf-5387-4d25-b4fb-cd1847d2c66b",
    ):
        record = next(item for item in deep if item.case_id == f"lexrel.{suffix}")
        assert record.claims[0].requested_remedy == UNANNOTATED_REMEDY
        assert record.claims[0].amount is None
        assert record.claims[0].invoked_rule_ids == []
        assert len(record.condition_findings) == 6
