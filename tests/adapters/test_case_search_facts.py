"""Production facts coverage without adding new adjudication/role fields to text."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from casepath.adapters.retrieval.case_facts import extract_case_fact_prefix
from casepath.adapters.retrieval.retrieval_catalog import (
    MemoryHybridSearch,
    _case_search_text,
    build_catalog,
)
from casepath.adapters.retrieval.vector_search import VectorIndex
from casepath.contracts import (
    CaseIndexRecord,
    CaseRecord,
    ClaimRecord,
    ConditionFinding,
    ConditionStatus,
    CourtFinding,
    DecisionItem,
    DecisionStatus,
    MaturityLevel,
    ReasoningStep,
    SourceSpan,
)
from casepath.evaluation.case_text import build_case_search_text
from casepath.ingestion.laws.jsonl import read_jsonl
from casepath.ports.embedding import EmbeddingSpec


def _light(facts="甲交付斑马矿石后，乙没有支付货款。", **extra):
    return CaseIndexRecord(
        case_id="case.facts.target", title="合同纠纷", facts_text=facts, **extra
    )


def _deep():
    quote = "SOURCE_QUOTE_NOT_SEARCHABLE"
    return CaseRecord(
        case_id="case.facts.target",
        title="DEEP_TITLE_NOT_ADDED",
        maturity=MaturityLevel.L1,
        claims=[
            ClaimRecord(
                claim_id="claim.private.target",
                claim_type="CLAIM_LABEL_NOT_SEARCHABLE",
                requested_remedy="REMEDY_NOT_SEARCHABLE",
                invoked_rule_ids=["rule.private.target"],
            )
        ],
        findings=[
            CourtFinding(
                finding_id="finding.facts", predicate="双方约定现金结算。",
                source_span_ids=["span.source"],
            )
        ],
        condition_findings=[
            ConditionFinding(
                condition_id="cond.private.target",
                status=ConditionStatus.SATISFIED,
                confidence=0.9,
                finding_ids=["finding.facts"],
                source_span_ids=["span.source"],
            )
        ],
        reasoning_steps=[
            ReasoningStep(
                reasoning_id="reasoning.target",
                applied_rule_ids=["rule.private.target"],
                conclusion="REASONING_NOT_SEARCHABLE",
                source_span_ids=["span.source"],
            )
        ],
        decisions=[
            DecisionItem(
                decision_id="decision.target",
                claim_id="claim.private.target",
                status=DecisionStatus.GRANTED,
                description="OUTCOME_NOT_SEARCHABLE",
                source_span_ids=["span.source"],
            )
        ],
        source_spans=[
            SourceSpan(
                span_id="span.source", source_id="case.facts.target",
                start_offset=0, end_offset=len(quote), quote=quote,
            )
        ],
    )


def _catalog(light):
    other = CaseIndexRecord(
        case_id="case.facts.other", title="另一合同纠纷", facts_text="甲向乙承租房屋。"
    )
    return build_catalog(
        release_id="facts-test-release", rules=[], provisions=[], lightweight=[light, other],
        deep=[],
    )


def test_fact_body_not_metadata_is_retrievable_by_real_memory_search():
    light = _light()
    catalog = _catalog(light)
    hits = MemoryHybridSearch(catalog).search(
        "斑马矿石", target="case", context=catalog.context(), top_k=5
    )
    assert [hit.object_id for hit in hits] == [light.case_id]
    assert light.facts_text in catalog.cases[light.case_id].search_text


def test_fact_prefix_preserves_legacy_metadata_and_findings_without_mutating_records():
    light = _light(cause="买卖合同纠纷", keywords=["货款", "交货"])
    deep = _deep()
    before = (light.model_dump(), deep.model_dump())
    text = _case_search_text(light, deep)
    assert text == "\n".join(
        [light.title, light.cause, *light.keywords, light.facts_text, "双方约定现金结算。"]
    )
    assert (light.model_dump(), deep.model_dump()) == before


def test_no_new_claim_condition_role_reasoning_outcome_or_quote_fields_added():
    light = _light(summary="SUMMARY_NOT_SEARCHABLE", reasoning_text="REASONING_BODY_NOT_SEARCHABLE")
    text = _case_search_text(light, _deep())
    for forbidden in (
        "CLAIM_LABEL_NOT_SEARCHABLE", "REMEDY_NOT_SEARCHABLE", "cond.private.target",
        "rule.private.target", "SATISFIED", "GRANTED", "REASONING_NOT_SEARCHABLE",
        "OUTCOME_NOT_SEARCHABLE", "SOURCE_QUOTE_NOT_SEARCHABLE", "SUMMARY_NOT_SEARCHABLE",
        "REASONING_BODY_NOT_SEARCHABLE", "DEEP_TITLE_NOT_ADDED",
    ):
        assert forbidden not in text


@pytest.mark.parametrize(
    "suffix",
    [
        "【裁判结果】秘密结果标记。",
        "\n## 裁判理由\n秘密结果标记。",
        "法院认为秘密结果标记。",
        "法院依法判令秘密结果标记。",
        "二审法院判决秘密结果标记。",
        "原告请求退款，法院判决秘密结果标记。",
    ],
)
def test_recognized_outcome_suffix_is_not_added_to_production_text(suffix):
    facts = "双方约定先交付货物后付款。"
    light = _light(facts + suffix)
    text = _case_search_text(light, None)
    assert facts in text
    assert "秘密结果标记" not in text
    assert extract_case_fact_prefix(light.facts_text) == facts


@pytest.mark.parametrize("facts", ["", " \r\n ", "法院认为应当支付货款。"])
def test_metadata_only_records_remain_compatible_but_evaluation_is_strict(facts):
    light = _light(facts, cause="买卖合同纠纷", keywords=["付款"])
    assert extract_case_fact_prefix(facts) == ""
    assert _case_search_text(light, None) == "合同纠纷\n买卖合同纠纷\n付款"
    with pytest.raises(ValueError, match="no evaluation facts prefix"):
        build_case_search_text(light)


def test_requested_judgment_is_retained_as_pleading_not_an_issued_outcome():
    facts = "原告请求法院依法作出民事判决：支付欠款。"
    assert extract_case_fact_prefix(facts) == facts
    assert facts in _case_search_text(_light(facts), None)


def test_editorial_marker_and_crlf_do_not_become_search_features():
    facts = "【编辑摘要，非判决原文】甲先交付货物。\r\n乙拒绝支付货款。"
    text = _case_search_text(_light(facts), None)
    assert "【编辑摘要，非判决原文】" not in text
    assert "\r" not in text
    assert "甲先交付货物。\n乙拒绝支付货款。" in text


def test_facts_changes_invalidate_existing_vector_index_without_rebuilding_it():
    class FixedEmbedding:
        spec = EmbeddingSpec("synthetic-test", "1", 2)

        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    model = FixedEmbedding()
    before = _catalog(_light("甲交付斑马矿石。"))
    # Synthetic local vectors only: no SDK, remote embedding or published index is used.
    old_index = VectorIndex.build(before, model)
    after = _catalog(_light("甲交付棕榈木材。"))
    assert before.release_id == after.release_id
    assert before.index_version != after.index_version
    with pytest.raises(ValueError, match="必须重建"):
        old_index.validate(after, model)


def test_shared_helper_preserves_all_156_existing_evaluation_texts_byte_for_byte():
    root = Path(__file__).resolve().parents[2]
    rows = read_jsonl(root / "data/canonical/cases/cases_l1.jsonl", CaseIndexRecord)
    texts = {row.case_id: build_case_search_text(row) for row in rows}
    payload = json.dumps(
        texts, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert len(texts) == 156
    # Frozen before moving the evaluator implementation into the shared helper.
    assert hashlib.sha256(payload).hexdigest() == (
        "3a801fda4b5a5f8a93538d2ea344ddba4b48b5303621f96493e8653dd164f0b0"
    )
    for row in rows:
        assert texts[row.case_id] == extract_case_fact_prefix(row.facts_text)
        assert texts[row.case_id] in _case_search_text(row, None)
