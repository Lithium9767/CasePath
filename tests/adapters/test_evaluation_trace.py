"""Observer tests use synthetic IDs/facts, never evaluation labels as rankings."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError
from threading import Barrier

import pytest

from casepath.adapters.retrieval.evaluation_trace import (
    capture_retrieval_rankings,
    record_retrieval_ranking,
)
from casepath.adapters.retrieval.graph_navigator import GraphNavigator, MemoryGraphNavigator
from casepath.adapters.retrieval.hybrid_cypher import (
    HybridCypherCaseRetriever,
    HybridCypherRuleRetriever,
    HybridCypherSettings,
)
from casepath.adapters.retrieval.retrieval_catalog import (
    CaseAssessment,
    CaseDocument,
    ConditionCard,
    ConditionGroupSpec,
    RetrievalCatalog,
    RuleDocument,
    bind_context,
    bind_round,
    bind_scope,
    current_context,
    current_round,
    current_scope,
    reset_context,
    reset_round,
    reset_scope,
    scored_reference,
)
from casepath.application.errors import RetrieverUnavailable
from casepath.contracts import ConditionStatus, QueryConditionState, QueryState


def test_no_observer_does_not_consume_ids_or_scores():
    def forbidden():
        raise AssertionError("disabled observers must not iterate")
        yield "unreachable"

    record_retrieval_ranking("a.final", forbidden(), scores=forbidden())


def test_capture_copies_order_and_scores_without_sorting_or_deduplicating():
    ids = ["rule.z", "rule.a", "rule.z"]
    scores = [0.2, 0.7, 0.2]
    with capture_retrieval_rankings() as trace:
        record_retrieval_ranking("a.final", ids, scores=scores)
    ids.reverse()
    ids.append("rule.new")
    scores.clear()
    assert trace.ids("a.final") == ("rule.z", "rule.a", "rule.z")
    assert trace.rankings["a.final"].scores == (0.2, 0.7, 0.2)
    assert trace.rankings["a.final"].stage == "a.final"
    with pytest.raises(TypeError):
        trace.rankings["a.final"] = None
    with pytest.raises(FrozenInstanceError):
        trace.rankings["a.final"].stage = "changed"


def test_observed_empty_is_distinct_from_missing_stage():
    with capture_retrieval_rankings() as trace:
        record_retrieval_ranking("a.final", [])
    assert trace.ids("a.final") == ()
    with pytest.raises(KeyError):
        trace.ids("c.fused")


def test_repeated_stage_keeps_latest_snapshot():
    with capture_retrieval_rankings() as trace:
        record_retrieval_ranking("a.final", ["rule.before"])
        record_retrieval_ranking("a.final", ["rule.after"])
    assert trace.ids("a.final") == ("rule.after",)


def test_nested_captures_are_isolated_and_restore_parent():
    with capture_retrieval_rankings() as outer:
        record_retrieval_ranking("a.final", ["rule.outer"])
        with capture_retrieval_rankings() as inner:
            record_retrieval_ranking("a.final", ["rule.inner"])
        record_retrieval_ranking("c.fused", ["case.outer"])
    assert inner.ids("a.final") == ("rule.inner",)
    assert set(inner.rankings) == {"a.final"}
    assert outer.ids("a.final") == ("rule.outer",)
    assert outer.ids("c.fused") == ("case.outer",)


def test_exception_discards_partial_inner_capture_but_restores_parent():
    with capture_retrieval_rankings() as outer:
        record_retrieval_ranking("a.final", ["rule.outer"])
        with (
            pytest.raises(RuntimeError, match="retrieval failed"),
            capture_retrieval_rankings() as inner,
        ):
            record_retrieval_ranking("a.final", ["rule.partial"])
            raise RuntimeError("retrieval failed")
        record_retrieval_ranking("c.fused", ["case.outer"])
    assert not inner.rankings
    assert outer.ids("c.fused") == ("case.outer",)


def test_exception_leaves_no_observer_for_later_calls():
    with pytest.raises(RuntimeError), capture_retrieval_rankings() as trace:
        record_retrieval_ranking("a.final", ["rule.partial"])
        raise RuntimeError("failed")
    record_retrieval_ranking("a.final", ["rule.unobserved"])
    assert not trace.rankings


def test_thread_captures_do_not_mix_rankings():
    barrier = Barrier(2)

    def work(identifier):
        with capture_retrieval_rankings() as trace:
            record_retrieval_ranking("a.final", [identifier])
            barrier.wait(timeout=10)
            record_retrieval_ranking("c.fused", [f"case.{identifier}"])
        return trace

    with capture_retrieval_rankings() as outer, ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(work, ["rule.one", "rule.two"]))
    assert not outer.rankings
    assert first.ids("a.final") == ("rule.one",)
    assert second.ids("a.final") == ("rule.two",)


def test_async_request_captures_do_not_mix_rankings():
    async def work(identifier):
        with capture_retrieval_rankings() as trace:
            record_retrieval_ranking("a.final", [identifier])
            await asyncio.sleep(0)
            record_retrieval_ranking("c.fused", [f"case.{identifier}"])
        return trace

    async def run():
        with capture_retrieval_rankings() as outer:
            first, second = await asyncio.gather(work("rule.one"), work("rule.two"))
        assert not outer.rankings
        return first, second

    first, second = asyncio.run(run())
    assert first.ids("a.final") == ("rule.one",)
    assert second.ids("a.final") == ("rule.two",)


def _case(case_id, status=None):
    assessments = () if status is None else (
        CaseAssessment(
            case_id=case_id,
            condition_id="cond.synthetic",
            status=status,
            finding_ids=(f"finding.{case_id}",),
            span_ids=(f"span.{case_id}",),
        ),
    )
    return CaseDocument(
        case_id=case_id,
        title="Synthetic test title",
        search_text="SENSITIVE_TEST_FACT_DO_NOT_CAPTURE",
        cited_provision_ids=(),
        applied_rule_ids=("rule.synthetic",) if status is not None else (),
        invoked_rule_ids=(),
        assessments=assessments,
        claims=(),
        decisions=(),
        reasoning=(),
        span_ids=(f"span.{case_id}",),
        finding_predicates=(),
        finding_ids=(f"finding.{case_id}",),
    )


def _catalog():
    rule = RuleDocument(
        rule_id="rule.synthetic",
        title="Synthetic rule",
        claim_types=(),
        condition_ids=("cond.synthetic",),
        core_ids=("cond.synthetic",),
        diagnostic_ids=(),
        groups=(ConditionGroupSpec("group.synthetic", "CORE", "ALL", ("cond.synthetic",)),),
        provision_ids=(),
        span_ids=("span.rule",),
        search_text="Synthetic rule text",
        maturity="L3",
    )
    cases = [
        _case("case.unknown"),
        _case("case.limiting", ConditionStatus.NOT_SATISFIED),
        _case("case.support.a", ConditionStatus.SATISFIED),
        _case("case.support.b", ConditionStatus.SATISFIED),
    ]
    return RetrievalCatalog(
        release_id="trace-test-release",
        index_version="trace-test-index",
        embedding_model="none",
        rules={rule.rule_id: rule},
        cases={case.case_id: case for case in cases},
        conditions={
            "cond.synthetic": ConditionCard(
                "cond.synthetic", "Synthetic condition", "Synthetic predicate", (), ()
            )
        },
        rule_ids_by_condition={"cond.synthetic": (rule.rule_id,)},
        provision_text={},
    )


class _FixedSearch:
    fail_cases = False

    def search(self, text, *, target, context, top_k):
        if target == "case" and self.fail_cases:
            raise RuntimeError("synthetic failure")
        ids = ["rule.synthetic"] if target == "rule" else [
            "case.unknown", "case.limiting", "case.support.a", "case.support.b"
        ]
        return [
            scored_reference(oid, 1.0 / rank, channels=["bm25"], components=[], reasons=[])
            for rank, oid in enumerate(ids[:top_k], start=1)
        ]


class _EmptyNavigator(GraphNavigator):
    def neighbor_rules(self, context, rule_ids):
        return []

    def cases_by_conditions(self, context, condition_ids):
        return []

    def cases_applying_rules(self, context, rule_ids):
        return []

    def cases_citing_provisions(self, context, rule_ids):
        return []


def _retrievers(*, graph=True, pool_size=4):
    catalog = _catalog()
    navigator = MemoryGraphNavigator(catalog) if graph else _EmptyNavigator()
    search = _FixedSearch()
    settings = HybridCypherSettings(
        rule_top_k=1, case_recall_top_k=4, case_rerank_pool_size=pool_size, case_role_top_k=1
    )
    rule_retriever = HybridCypherRuleRetriever(
        navigator, search, settings, catalog=catalog, navigator=navigator
    )
    case_retriever = HybridCypherCaseRetriever(
        navigator, search, settings, catalog=catalog, navigator=navigator
    )
    return catalog, rule_retriever, case_retriever


def _state():
    return QueryState(
        session_id="trace-test",
        initial_query="SENSITIVE_QUERY_DO_NOT_CAPTURE",
        condition_states=[
            QueryConditionState(
                condition_id="cond.synthetic", status=ConditionStatus.SATISFIED
            )
        ],
    )


def _run(catalog, rule_retriever, case_retriever):
    context_token = bind_context(catalog.context())
    round_token = bind_round(None)
    scope_token = bind_scope(None)
    try:
        rules = rule_retriever.retrieve(_state())
        bundle = case_retriever.retrieve(_state(), rules)
        return (
            rules, bundle, deepcopy(current_context()), deepcopy(current_scope()),
            deepcopy(current_round()),
        )
    finally:
        reset_scope(scope_token)
        reset_round(round_token)
        reset_context(context_token)


@pytest.mark.parametrize("graph", [False, True])
@pytest.mark.parametrize("pool_size", [2, 4])
def test_capture_leaves_real_ac_outputs_and_request_evidence_unchanged(graph, pool_size):
    catalog, rule_retriever, case_retriever = _retrievers(graph=graph, pool_size=pool_size)
    baseline = _run(catalog, rule_retriever, case_retriever)
    before_context = (current_context(), current_scope(), current_round())
    with capture_retrieval_rankings() as trace:
        observed = _run(catalog, rule_retriever, case_retriever)
    assert observed == baseline
    assert before_context == (current_context(), current_scope(), current_round())
    rules, bundle, _, _, evidence = observed
    assert trace.ids("a.final") == tuple(ref.object_id for ref in rules)
    assert trace.rankings["a.final"].scores == tuple(ref.score for ref in rules)
    assert trace.ids("c.pool") == evidence.fused_case_ids
    for role in ("support", "limiting", "boundary"):
        refs = getattr(bundle, f"{role}_case_refs")
        assert trace.ids(f"c.{role}") == tuple(ref.object_id for ref in refs)
        assert trace.rankings[f"c.{role}"].scores == tuple(ref.score for ref in refs)
    assert set(trace.ids("c.undetermined")) == evidence.undetermined_case_ids
    assert len(trace.ids("c.fused")) == 4
    assert "SENSITIVE_QUERY_DO_NOT_CAPTURE" not in repr(dict(trace.rankings))
    assert "SENSITIVE_TEST_FACT_DO_NOT_CAPTURE" not in repr(dict(trace.rankings))
    if graph:
        assert evidence.paths_by_case


def test_c_fused_preserves_full_rrf_order_before_role_pool_and_output_limits():
    retrievers = _retrievers(graph=False, pool_size=2)
    with capture_retrieval_rankings() as trace:
        _run(*retrievers)
    assert trace.ids("c.fused") == (
        "case.unknown", "case.limiting", "case.support.a", "case.support.b"
    )
    assert trace.rankings["c.fused"].scores == pytest.approx(
        (1 / 61, 1 / 62, 1 / 63, 1 / 64)
    )
    # Existing role diversification is retained, never sorted by the observer.
    assert trace.ids("c.pool") == ("case.support.a", "case.limiting")
    assert trace.ids("c.support") == ("case.support.a",)
    assert trace.ids("c.limiting") == ("case.limiting",)
    assert trace.ids("c.boundary") == ()
    assert trace.ids("c.undetermined") == ()


def test_trace_keeps_unknown_cases_separate_from_role_lists():
    with capture_retrieval_rankings() as trace:
        _run(*_retrievers(graph=False))
    assert trace.ids("c.undetermined") == ("case.unknown",)
    assert "case.unknown" in trace.ids("c.pool")
    assert "case.unknown" in trace.ids("c.fused")
    assert all(
        "case.unknown" not in trace.ids(stage)
        for stage in ("c.support", "c.limiting", "c.boundary")
    )


def test_all_channel_stages_record_even_empty_rankings():
    with capture_retrieval_rankings() as trace:
        _run(*_retrievers(graph=False))
    for stage in (
        "a.channel.bm25", "a.channel.vector", "a.channel.claim", "a.channel.graph",
        "a.fused", "a.final", "c.channel.bm25", "c.channel.vector", "c.channel.graph",
        "c.channel.applies", "c.channel.cites", "c.fused", "c.pool", "c.support",
        "c.limiting", "c.boundary", "c.undetermined",
    ):
        assert stage in trace.rankings
    assert trace.ids("a.channel.bm25") == ("rule.synthetic",)
    assert trace.ids("c.channel.vector") == ()
    assert trace.rankings["c.channel.bm25"].scores == ()


def test_failed_case_retrieval_discards_earlier_rule_trace():
    catalog, rules, cases = _retrievers()
    cases.search.fail_cases = True
    with pytest.raises(RetrieverUnavailable), capture_retrieval_rankings() as trace:
        _run(catalog, rules, cases)
    assert not trace.rankings
    cases.search.fail_cases = False
    _run(catalog, rules, cases)
    assert not trace.rankings
