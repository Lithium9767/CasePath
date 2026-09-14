from __future__ import annotations

import json
from pathlib import Path

import pytest

from casepath.contracts import CaseIndexRecord
from casepath.evaluation.case_text import build_case_search_text


def case(facts: str, **extra) -> CaseIndexRecord:
    return CaseIndexRecord(
        case_id="case.evaluation.text",
        title="隐藏的答案标题",
        facts_text=facts,
        **extra,
    )


def test_only_facts_are_indexed_and_input_unchanged():
    record = case(
        "当事人交付货物后一直未收到货款。",
        cause="隐藏案由",
        keywords=["隐藏关系标签"],
        summary="隐藏判决摘要",
        reasoning_text="法院判令支付货款。",
    )
    before = record.model_dump()
    assert build_case_search_text(record) == record.facts_text
    assert record.model_dump() == before


@pytest.mark.parametrize(
    "suffix",
    [
        "法院认为应承担违约责任。",
        "法院依法判令支付300元。",
        "法院驳回全部请求。",
        "本院经审理认为应予支持。",
        "北京市某区人民法院于2023年1月1日作出民事判决：支付300元。",
        "二审法院判决维持原判。",
        "终审裁定驳回申请。",
        "判决如下：支付300元。",
        "裁定：驳回申请。",
        "【裁判结果】支付300元。",
        "## 裁判理由\n违约成立。",
    ],
)
def test_explicit_reasoning_or_disposition_suffix_is_excluded(suffix):
    assert build_case_search_text(case("双方约定分期还款。\n" + suffix)) == "双方约定分期还款。"


def test_requested_judgment_is_not_a_disposition():
    facts = "原告诉请法院作出民事判决：退还尚未消费的预付款。"
    assert build_case_search_text(case(facts)) == facts


def test_actual_disposition_after_pleading_in_same_sentence_is_removed():
    facts = "甲先付款。原告请求退款，法院判决返还全部款项。"
    assert build_case_search_text(case(facts)) == "甲先付款。"


@pytest.mark.parametrize("prefix", ["原告请求", "原告诉称：请求", "原告要求"])
def test_only_immediately_requested_judgment_is_exempt(prefix):
    facts = prefix + "法院依法作出民事判决：退还费用。"
    assert build_case_search_text(case(facts)) == facts


def test_findings_and_user_request_are_retained():
    facts = "法院经审理查明：经营者收款后停业。原告请求判令返还费用。"
    assert build_case_search_text(case(facts)) == facts


def test_reference_to_a_result_is_not_a_section_heading():
    facts = "当事人要求获知裁判结果的通知方式，并补交了合同。"
    assert build_case_search_text(case(facts)) == facts


def test_editorial_marker_is_not_a_search_feature():
    assert build_case_search_text(case("【编辑摘要，非判决原文】甲交付货物。")) == "甲交付货物。"


@pytest.mark.parametrize("facts", ["", " \n ", "法院认为应返还费用。"])
def test_empty_facts_do_not_fall_back_to_title_or_labels(facts):
    with pytest.raises(ValueError, match="no evaluation facts prefix"):
        build_case_search_text(case(facts, keywords=["借款合同关系"]))


def test_all_published_cases_have_a_nonempty_evaluation_view():
    root = Path(__file__).resolve().parents[2]
    path = root / "data/canonical/cases/cases_l1.jsonl"
    records = [
        CaseIndexRecord.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 156
    for record in records:
        assert build_case_search_text(record).strip()
