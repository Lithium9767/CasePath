from pathlib import Path

from casepath.contracts import ConditionStatus, MaturityLevel
from casepath.ingestion.cases.extract import (
    SpanBook,
    applied_rule_ids_from_reasoning,
    extract_condition_findings,
    extract_findings_from_facts,
)
from casepath.ingestion.cases.lexrel import load_service_contract_cases
from casepath.ingestion.cases.refinements import (
    SERVICE_REFUND_COMPARISON_CASE_IDS,
    SERVICE_REFUND_RULE_CASE_IDS,
)
from casepath.rule_layer.ids import (
    COND_ALTERNATIVE_PERFORMANCE,
    COND_PAYMENT_MADE,
    COND_PERFORMANCE_IMPOSSIBLE,
    COND_UNPERFORMED_BALANCE,
    RULE_SERVICE_TERMINATION_REFUND,
)

LEXREL_INPUT = Path(__file__).resolve().parents[3] / "data/inputs/lexrel_service_contract.json"


def test_refund_comparison_set_is_curated_and_anchored():
    _, deep = load_service_contract_cases(LEXREL_INPUT)
    by_id = {record.case_id: record for record in deep}
    for case_id in SERVICE_REFUND_COMPARISON_CASE_IDS:
        if not case_id.startswith("lexrel."):
            continue
        record = by_id[case_id]
        assert record.maturity == MaturityLevel.L1
        assert len(record.condition_findings) == 6
        assert COND_ALTERNATIVE_PERFORMANCE in {
            item.condition_id for item in record.condition_findings
        }
        known = [
            item for item in record.condition_findings if item.status != ConditionStatus.UNKNOWN
        ]
        assert all(item.source_span_ids for item in known)
        assert all(item.human_verified is False for item in record.condition_findings)
        # A comparison/template ID alone is not evidence of an expressed claim.
        # The gym snapshot has a payment and closure, but no refund request.
        if (
            case_id in SERVICE_REFUND_RULE_CASE_IDS
            and "主张退还" in record.claims[0].requested_remedy
        ):
            assert record.claims[0].invoked_rule_ids == [RULE_SERVICE_TERMINATION_REFUND]
        else:
            assert RULE_SERVICE_TERMINATION_REFUND not in record.claims[0].invoked_rule_ids


def test_non_comparison_lexrel_cases_have_no_refund_conditions():
    _, deep = load_service_contract_cases(LEXREL_INPUT)
    for record in deep:
        if record.case_id in SERVICE_REFUND_COMPARISON_CASE_IDS:
            continue
        assert record.condition_findings == []
        assert RULE_SERVICE_TERMINATION_REFUND not in record.claims[0].invoked_rule_ids


def test_ctrip_keeps_uncertain_impossible_and_unperformed_labels():
    _, deep = load_service_contract_cases(LEXREL_INPUT)
    ctrip = next(
        record for record in deep if record.case_id.endswith("829d6968-3b55-43c8-9e18-9d6ccccfd391")
    )
    by_cond = {item.condition_id: item for item in ctrip.condition_findings}
    # Facts show a prior refund request and a remaining balance, not that the
    # later balance of 17,541 was itself requested as a litigation amount.
    assert ctrip.claims[0].amount is None
    assert by_cond[COND_PERFORMANCE_IMPOSSIBLE].status == ConditionStatus.UNKNOWN
    assert by_cond[COND_UNPERFORMED_BALANCE].status == ConditionStatus.UNKNOWN
    assert ctrip.decisions == []
    assert ctrip.reasoning_steps == []


def test_purchase_language_alone_does_not_prove_payment():
    facts = "法院经审理查明：双方签订《会员协议》，约定曾某购买会籍服务以及私人教练服务。门店关闭。"
    book = SpanBook("case.payment-anti")
    findings = extract_findings_from_facts("case.payment-anti", facts, book)
    # Force a court-style sentence list through the refund bridge path.
    bridges = extract_condition_findings(findings, facts, "", "", book)
    payment = next(item for item in bridges if item.condition_id == COND_PAYMENT_MADE)
    assert payment.status == ConditionStatus.UNKNOWN


def test_applies_is_not_copied_from_invokes():
    assert applied_rule_ids_from_reasoning("原告请求解除合同并返还费用。") == []
    assert applied_rule_ids_from_reasoning(
        "原告曾某根据《中华人民共和国民法典》第五百六十三条要求解除《会员协议》，符合法律规定。"
    ) == [RULE_SERVICE_TERMINATION_REFUND]


def test_applies_rejects_denial_party_statement_and_hypothesis():
    texts = [
        "原告主张解除合同符合法律规定，但本院不予支持。",
        "本案不适用根据《中华人民共和国民法典》第五百六十六条返还费用的规则。",
        "原告主张根据《中华人民共和国民法典》第五百六十六条应予返还费用。",
        "原告根据《中华人民共和国民法典》第五百六十三条要求解除合同，符合法律规定。本院不予支持。",
        "如果根据《中华人民共和国民法典》第五百六十六条应予返还费用，则另行处理。",
        "解除劳动合同符合法律规定。",
    ]
    for text in texts:
        assert applied_rule_ids_from_reasoning(text) == [], text


def test_applies_span_is_the_matching_sentence():
    from casepath.ingestion.cases.extract import extract_reasoning_step

    quote = "根据《中华人民共和国民法典》第五百六十六条规定，被告应予返还原告交纳的会籍费用。"
    text = "双方对解除合同存在争议。" + quote
    book = SpanBook("case.application-span")
    step = extract_reasoning_step("case.application-span", text, [], book)
    assert step is not None
    assert step.applied_rule_ids == [RULE_SERVICE_TERMINATION_REFUND]
    assert step.conclusion == quote
    assert book.spans[0].quote == quote
    assert text[book.spans[0].start_offset : book.spans[0].end_offset] == quote
