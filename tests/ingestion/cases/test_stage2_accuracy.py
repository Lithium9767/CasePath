"""Stage-two source-grounded extraction checks; no legal outcome inference."""

from __future__ import annotations

from pathlib import Path

import pytest

from casepath.contracts import DecisionStatus, MaturityLevel
from casepath.ingestion.cases.cleaner import CaseCleaner
from casepath.ingestion.cases.extract import SpanBook, extract_findings_from_facts
from casepath.ingestion.cases.lexrel import load_service_contract_cases
from casepath.ingestion.cases.sections import (
    parse_amount,
    parse_granted_amount,
    parse_requested_amount,
    split_fact_partitions,
)

ROOT = Path(__file__).resolve().parents[3]
PROCESSED = ROOT.parent / "legal-rag/data/processed/processed_cases.json"
LEXREL = ROOT / "data/inputs/lexrel_service_contract.json"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("人民币14789.03元", 14789.03),
        ("125984.65元", 125984.65),
        ("4,079,086.97元", 4079086.97),
        ("5，062元", 5062),
        ("58，470元", 58470),
        ("17，541.25元", 17541.25),
        ("6万元", 60000),
        ("0.125万元", 1250),
        ("2万余元", None),
        ("3，62元", None),
    ],
)
def test_exact_money_preserves_decimal_grouping_and_units(text, expected):
    assert parse_amount(text) == expected


def test_requested_amount_comes_from_demand_after_payment_history():
    text = (
        "原告已支付旅游费58，470元。被告退回40，929元。"
        "请求判令：被告退还剩余服务款17，541元。"
    )
    assert parse_requested_amount(text) == 17541
    assert parse_requested_amount("借款本金数额为38298.65元。") is None
    assert parse_requested_amount("合同约定年使用费为24600元。") is None


def test_requested_amount_can_follow_numbered_termination_demand():
    assert parse_requested_amount(
        "请求判令：1.解除会员协议；2.被告返还款项24,560元；3.本案诉讼费207元由被告承担。"
    ) == 24560


def test_explicit_total_takes_priority_over_individual_costs():
    assert parse_requested_amount(
        "请求判令补偿医疗费5244.28元、交通费225.5元等，合计15180.68元。"
    ) == 15180.68
    assert parse_granted_amount(
        "被告赔偿环境修复费用2168000元、其他费用等，以上共计3025071.91元。"
    ) == 3025071.91


@pytest.mark.parametrize(
    "text",
    [
        "偿还贷款本金179994136.33元及利息（包括罚息、复利）合计60345080.83元。",
        "偿还本金1000元及利息合计1200元。",
        "支付第一项款项7805034.77元；支付第二项本息合计4038216.67元。",
        "赔偿经济损失2000元；赔偿维权费用5206元。",
        "支付代偿款125984.65元；在62992.325元限额内承担连带责任。",
        "支付费用1000元；另支付费用1000元。",
    ],
)
def test_competing_amounts_and_subtotals_are_not_an_overall_amount(text):
    assert parse_granted_amount(text) is None
    assert parse_requested_amount("请求判令：" + text) is None


@pytest.mark.parametrize(
    "text",
    [
        "支付每月800元的抚养费。",
        "按每月800元的标准支付抚养费。",
        "支付自2023年1月1日起至实际履行日止按照每月500元计算的土地占有使用费。",
        "支付每年24600元的服务费。",
        "按日支付21.78元。",
        "每月共计支付800元。",
    ],
)
def test_periodic_prices_are_not_lump_sum_amounts(text):
    assert parse_granted_amount(text) is None
    assert parse_requested_amount("请求判令：" + text) is None


def test_explicit_total_of_distinct_awards_is_retained():
    text = "支付租金6万元、赔偿损失5万元，共计11万元。"
    assert parse_granted_amount(text) == 110000
    assert parse_requested_amount("请求判令：" + text) == 110000


def test_post_judgment_reward_is_not_part_of_the_award():
    text = "支付补偿款7000元。宣判后，判决已发生法律效力。有关单位给予奖励1000元。"
    assert parse_granted_amount(text) == 7000


def test_request_for_a_judgment_is_not_a_judgment():
    parts = split_fact_partitions("原告甲诉称：请求法院判决：乙返还1200元。")
    assert parts.holding == ""
    assert split_fact_partitions("原告甲请求法院作出判决：乙支付1200元。").holding == ""


@pytest.mark.parametrize(
    "text",
    [
        "驳回原告的诉讼请求。案件受理费100元，由原告负担。",
        "驳回原告要求被告返还1200元的诉讼请求。",
        "驳回上诉，维持原判。",
    ],
)
def test_denied_demand_and_costs_are_not_awarded_amounts(text):
    assert parse_granted_amount(text) is None


def test_later_judgment_is_selected_without_turning_prior_holding_into_findings():
    text = (
        "原告甲诉称：请求乙支付125984.65元。被告乙辩称：不同意。"
        "法院经审理查明：甲曾为乙提供借款保证。"
        "法院作出旧案民事判决：乙返还银行90000元。"
        "甲已履行保证责任。法院做出本案民事判决：乙支付甲125984.65元。"
    )
    parts = split_fact_partitions(text)
    assert parts.holding == "乙支付甲125984.65元。"
    assert parts.court == "甲曾为乙提供借款保证。"
    assert "125984.65元" in parts.plaintiff
    assert "旧案" not in parts.court


def test_comma_judgment_marker_selects_retrial_instead_of_reversed_ruling():
    text = (
        "法院作出民事判决：驳回原告诉讼请求。"
        "2022年9月8日作出民事判决，撤销原判；被告返还供热费594.30元。"
    )
    parts = split_fact_partitions(text)
    assert parts.holding == "撤销原判；被告返还供热费594.30元。"
    assert parse_granted_amount(parts.holding) == 594.3


def test_numbered_claim_is_not_an_affirmative_court_finding():
    text = "1. 双方签订培训服务合同。\n2. 原告向被告主张退还1，200元的培训费。"
    book = SpanBook("accuracy.numbered")
    findings = extract_findings_from_facts("accuracy.numbered", text, book)
    assert [item.predicate for item in findings] == ["1. 双方签订培训服务合同。"]
    assert len(book.spans) == 1
    span = book.spans[0]
    assert text[span.start_offset:span.end_offset] == span.quote


def test_explicit_result_section_is_used_and_kept_in_canonical_source_text():
    result = "一、撤销原判；二、被告向原告支付1250.25元。"
    case = {
        "case_id": "accuracy.result-section",
        "title": "甲诉乙合同纠纷案",
        "case_facts": "法院作出旧案民事判决：驳回原告诉讼请求。",
        "full_document": (
            "## 基本案情\n原告甲诉称：请求乙支付1250.25元。\n"
            "法院经审理查明：双方签订了服务合同。\n"
            "法院作出旧案民事判决：驳回原告诉讼请求。\n"
            f"## 裁判结果\n{result}\n## 裁判理由\n被告应支付费用。"
        ),
    }
    cleaner = CaseCleaner(Path("unused.json"))
    index, record = cleaner.lightweight(case), cleaner.deep_record(case)
    assert record.decisions[0].description == result
    assert record.decisions[0].amount == 1250.25
    assert result in index.facts_text
    for span in record.source_spans:
        document = index.reasoning_text if span.section == "裁判理由" else index.facts_text
        assert document[span.start_offset:span.end_offset] == span.quote


def test_complete_holding_keeps_monetary_evidence_after_240_characters():
    holding = "一、" + "甲应协助乙办理相关手续；" * 25 + "二、乙支付甲1250.25元。"
    case = {
        "case_id": "accuracy.long-result",
        "title": "甲诉乙合同纠纷案",
        "case_facts": f"原告甲诉称：请求乙支付1250.25元。法院作出民事判决：{holding}",
    }
    record = CaseCleaner(Path("unused.json")).deep_record(case)
    decision = record.decisions[0]
    assert decision.amount == 1250.25
    assert decision.description == holding
    assert record.source_spans[-1].quote.endswith("1250.25元。")


@pytest.fixture(scope="module")
def audited_civil_cases():
    if not PROCESSED.is_file():
        pytest.skip("pinned external legal-rag checkout is absent")
    cleaner = CaseCleaner(PROCESSED)
    cases = cleaner.civil_cases()
    assert len(cases) == 45
    return {
        case["title"]: (cleaner.lightweight(case), cleaner.deep_record(case))
        for case in cases
    }


@pytest.mark.parametrize(
    ("title", "amount", "evidence", "excluded"),
    [
        (
            "柴某诉顾某生命权、身体权、健康权纠纷案",
            7000,
            "支付原告柴某补偿款7000元",
            "",
        ),
        (
            "张某甲诉白某等追偿权纠纷案",
            None,
            "支付原告张某甲代偿款125984.65元",
            "返还某银行借款本金9万元",
        ),
        (
            "张某某诉王某某、王某、宋某军非机动车交通事故责任纠纷案",
            14789.03,
            "共计14789.03元",
            "",
        ),
        (
            "吴某某诉张某某、李某、某某青少年体育俱乐部、某财产保险股份有限公司北京市分公司生命权、身体权、健康权纠纷一案",
            53990.73,
            "向吴某某支付各项损失53990.73元",
            "合计43204.39元",
        ),
        (
            "单某某诉乳山某热力公司供用热力合同纠纷案",
            594.3,
            "返还单某某2020-2021年度供热费594.30元",
            "驳回单某某的诉讼请求",
        ),
        (
            "连云港某物流公司诉连云港某石油公司港口货物保管合同纠纷案",
            7661941.12,
            "赔偿7661941.12元",
            "赔偿9744872.62元",
        ),
    ],
)
def test_pinned_current_judgment_examples(audited_civil_cases, title, amount, evidence, excluded):
    index, record = audited_civil_cases[title]
    decision = record.decisions[0]
    assert decision.amount == amount
    assert evidence in decision.description
    assert evidence in index.facts_text
    if excluded:
        assert excluded not in decision.description


def test_pinned_reversed_marriage_judgment_does_not_keep_initial_rejection(audited_civil_cases):
    _, record = audited_civil_cases["赵某局诉潘某婷撤销婚姻纠纷案"]
    decision = record.decisions[0]
    assert decision.description == "撤销原判，改判撤销赵某局与潘某婷的婚姻。"
    assert decision.amount is None
    assert decision.status == DecisionStatus.UNKNOWN


def test_pinned_gym_refund_excludes_court_costs_from_requested_amount(audited_civil_cases):
    _, record = audited_civil_cases["曾某诉武汉某健身管理有限公司服务合同纠纷案"]
    assert record.claims[0].amount == 24560
    assert record.decisions[0].amount == 16370
    assert record.decisions[0].status == DecisionStatus.PARTIALLY_GRANTED


def test_all_54_pinned_case_spans_resolve_without_maturity_upgrade(audited_civil_cases):
    lexrel_index, lexrel_deep = load_service_contract_cases(LEXREL)
    pairs = list(audited_civil_cases.values()) + list(zip(lexrel_index, lexrel_deep, strict=True))
    assert len(pairs) == 54
    for index, record in pairs:
        assert record.maturity == MaturityLevel.L1
        assert all(not item.human_verified for item in record.condition_findings)
        for span in record.source_spans:
            document = index.reasoning_text if span.section == "裁判理由" else index.facts_text
            assert document[span.start_offset:span.end_offset] == span.quote, span.span_id
            assert span.end_offset - span.start_offset == len(span.quote)


def test_lexrel_loan_principal_does_not_become_service_claim_amount():
    _, records = load_service_contract_cases(LEXREL)
    by_id = {record.case_id: record for record in records}
    for uniqid in ("698f1ab4-dae7-47b5-a0b3-3b4c64f954aa", "aea77fcf-9dae-4e62-85b6-05c56d79e332"):
        record = by_id[f"lexrel.{uniqid}"]
        assert record.claims[0].amount is None
        assert record.decisions == []
    training = by_id["lexrel.1ec928ab-bb1e-4131-bafc-e17593454e1b"]
    assert not any("主张退还" in item.predicate for item in training.findings)
