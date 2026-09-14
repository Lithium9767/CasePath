"""Synthetic C query separation invariants; no benchmark queries or judgments."""

from __future__ import annotations

import pytest

from casepath.adapters.retrieval.evaluation_trace import capture_retrieval_rankings
from casepath.adapters.retrieval.graph_navigator import MemoryGraphNavigator
from casepath.adapters.retrieval.hybrid_cypher import (
    HybridCypherCaseRetriever,
    HybridCypherSettings,
    _case_query_variants,
    _query_text,
)
from casepath.adapters.retrieval.retrieval_catalog import (
    CaseDocument,
    RetrievalCatalog,
    RuleDocument,
    bind_context,
    bind_round,
    bind_scope,
    current_round,
    reset_context,
    reset_round,
    reset_scope,
    scored_reference,
)
from casepath.contracts import (
    CandidateClaim,
    ConditionEvidence,
    ConditionStatus,
    DialogueTurn,
    QueryConditionState,
    QueryState,
    UserFact,
)


def _state(**updates) -> QueryState:
    latest = max((fact.source_turn for fact in updates.get("user_facts", [])), default=0)
    if latest:
        conditions = updates.setdefault("condition_states", [QueryConditionState(
            condition_id="cond.synthetic", status=ConditionStatus.UNKNOWN,
        )])
        updates.setdefault("dialogue_history", [DialogueTurn(
            turn_id=turn, question_id=f"question.synthetic.{turn}",
            condition_id=conditions[0].condition_id, question="What happened next?",
            answer="A source-attributed follow-up answer.",
        ) for turn in range(1, latest + 1)])
    return QueryState(session_id="query-variant.synthetic", initial_query="orchard blossom", **updates)


def _claim(label="geology", remedy="mineral survey", confidence=0.7):
    return CandidateClaim(claim_type=label, requested_remedy=remedy, confidence=confidence)


def test_same_source_initial_registration_does_not_change_primary_query():
    before = _state()
    after = _state(user_facts=[UserFact(
        fact_id="fact.initial.query", text=before.initial_query, source_turn=0,
    )])
    assert _case_query_variants(after) == _case_query_variants(before)


@pytest.mark.parametrize("fact_id,turn,text", [
    ("fact.later", 1, "orchard blossom"),
    ("fact.other_speaker", 0, "orchard blossom"),
    ("fact.initial.query", 1, "orchard blossom"),
    ("fact.initial.query", 0, "not orchard blossom"),
    ("fact.correction", 2, "没有收到款项，对方说已经付款"),
])
def test_other_source_repetitions_and_negations_are_preserved(fact_id, turn, text):
    state = _state(user_facts=[UserFact(fact_id=fact_id, text=text, source_turn=turn)])
    primary, hypothesis = _case_query_variants(state)
    assert primary == state.initial_query + "\n" + text
    assert hypothesis == ""


def test_generic_consultation_summary_is_not_an_expansion():
    state = _state(candidate_claims=[_claim("民事咨询", "orchard", confidence=0.4)])
    assert _case_query_variants(state) == (state.initial_query, "")


def test_claim_text_already_in_user_statements_is_not_repeated_as_hypothesis():
    state = _state(
        user_facts=[UserFact(fact_id="fact.later", text="mineral survey", source_turn=1)],
        candidate_claims=[_claim("orchard", "mineral survey")],
    )
    assert _case_query_variants(state) == ("orchard blossom\nmineral survey", "")


def test_duplicate_claim_fields_and_claims_only_expand_once():
    state = _state(candidate_claims=[_claim("geology", "geology"), _claim("geology", "geology")])
    assert _case_query_variants(state) == (state.initial_query, "geology")


def test_query_variants_do_not_create_or_modify_facts_or_condition_evidence():
    state = _state(
        user_facts=[UserFact(fact_id="fact.reply", text="没有支付", source_turn=1)],
        candidate_claims=[_claim()],
        condition_states=[QueryConditionState(
            condition_id="cond.synthetic",
            status=ConditionStatus.NOT_SATISFIED,
            supporting_fact_ids=["fact.reply"],
            evidence=[ConditionEvidence(
                fact_id="fact.reply", relation="OPPOSES", confidence=0.8,
                reason="用户明确否定，不因候选诉求改变",
            )],
        )],
    )
    frozen = state.model_dump(mode="json")
    _case_query_variants(state)
    assert state.model_dump(mode="json") == frozen


@pytest.mark.parametrize("weight", [float("nan"), float("inf"), float("-inf"), -0.01, 1.01])
def test_hypothesis_weight_rejects_nonfinite_and_out_of_range(weight):
    with pytest.raises(ValueError, match="hypothesis weight"):
        HybridCypherSettings(case_hypothesis_weight=weight)


@pytest.mark.parametrize("weight", [0, 0.35, 1])
def test_hypothesis_weight_accepts_closed_interval(weight):
    assert HybridCypherSettings(case_hypothesis_weight=weight).case_hypothesis_weight == weight


def test_legacy_query_mode_is_default_until_separation_is_validated():
    assert HybridCypherSettings().case_query_mode == "legacy"


@pytest.mark.parametrize("mode", ["unknown", "", None])
def test_unknown_case_query_mode_is_rejected(mode):
    with pytest.raises(ValueError):
        HybridCypherSettings(case_query_mode=mode)


def _catalog() -> RetrievalCatalog:
    rule = RuleDocument(
        rule_id="rule.synthetic", title="Synthetic rule", claim_types=(),
        condition_ids=(), core_ids=(), diagnostic_ids=(), groups=(),
        provision_ids=(), span_ids=(), search_text="orchard blossom", maturity="L1",
    )
    cases = {
        case_id: CaseDocument(
            case_id=case_id, title="Synthetic case", search_text=text,
            cited_provision_ids=(), applied_rule_ids=(), invoked_rule_ids=(),
            assessments=(), claims=(), decisions=(), reasoning=(), span_ids=(),
            finding_predicates=(), finding_ids=(),
        )
        for case_id, text in (
            ("case.direct", "orchard blossom"),
            ("case.hypothesis", "geology mineral survey"),
        )
    }
    return RetrievalCatalog(
        release_id="query-variant.release", index_version="query-variant.index",
        embedding_model="none", rules={rule.rule_id: rule}, cases=cases,
        conditions={}, rule_ids_by_condition={}, provision_text={},
    )


class _CountingSearch:
    """Stand-in for a potentially remote vector/embedding-backed search."""

    def __init__(self):
        self.calls = []

    def search(self, text, *, target, context, top_k):
        self.calls.append((text, target, context, top_k))
        return [scored_reference(
            "case.direct", 1.0, channels=["vector"], components=[], reasons=[],
        )]


def _retrieve(*, mode="separated", weight=0.35):
    catalog = _catalog()
    state = _state(candidate_claims=[_claim()])
    search = _CountingSearch()
    settings = HybridCypherSettings(case_query_mode=mode, case_hypothesis_weight=weight)
    retriever = HybridCypherCaseRetriever(
        MemoryGraphNavigator(catalog), search, settings, catalog=catalog,
    )
    context_token = bind_context(catalog.context())
    round_token, scope_token = bind_round(None), bind_scope(None)
    frozen = state.model_dump(mode="json")
    try:
        with capture_retrieval_rankings() as trace:
            bundle = retriever.retrieve(state, [scored_reference(
                "rule.synthetic", 1.0, channels=["bm25"], components=[], reasons=[],
            )])
        assert state.model_dump(mode="json") == frozen
        return state, search, retriever, trace, bundle, current_round()
    finally:
        reset_scope(scope_token)
        reset_round(round_token)
        reset_context(context_token)


def test_claim_lexical_recall_uses_no_second_remote_search_and_invents_no_role():
    state, search, retriever, trace, bundle, evidence = _retrieve()
    assert len(search.calls) == 1
    assert search.calls[0][0] == state.initial_query
    assert search.calls[0][1] == "case"
    assert retriever._hypothesis_index is not None
    assert "case.hypothesis" in trace.ids("c.fused")
    assert evidence.channels_by_case["case.hypothesis"] == ("claim_lexical",)
    assert "case.hypothesis" in evidence.undetermined_case_ids
    assert not evidence.assessments_by_case.get("case.hypothesis")
    assert not evidence.paths_by_case.get("case.hypothesis")
    assert not bundle.support_case_refs
    assert not bundle.limiting_case_refs
    assert not bundle.boundary_case_refs


def test_zero_hypothesis_weight_does_not_build_auxiliary_index_or_recall_it():
    _, search, retriever, trace, _, _ = _retrieve(weight=0)
    assert len(search.calls) == 1
    assert retriever._hypothesis_index is None
    assert trace.ids("c.fused") == ("case.direct",)


def test_legacy_mode_preserves_original_query_and_has_no_auxiliary_call():
    state, search, retriever, trace, _, _ = _retrieve(mode="legacy")
    assert len(search.calls) == 1
    assert search.calls[0][0] == _query_text(state)
    assert retriever._hypothesis_index is None
    assert trace.ids("c.fused") == ("case.direct",)
