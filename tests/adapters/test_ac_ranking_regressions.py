"""Synthetic ranking invariants: no benchmark queries, labels or topic lookup tables."""

from __future__ import annotations

from dataclasses import replace

import pytest

from casepath.adapters.retrieval.evaluation_trace import capture_retrieval_rankings
from casepath.adapters.retrieval.graph_navigator import GraphHit, GraphNavigator
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
    current_round,
    reset_context,
    reset_round,
    reset_scope,
    scored_reference,
)
from casepath.contracts import ConditionStatus, QueryConditionState, QueryState


def _catalog() -> RetrievalCatalog:
    rule = RuleDocument(
        rule_id="rule.synthetic",
        title="Synthetic rule",
        claim_types=(),
        condition_ids=("cond.synthetic",),
        core_ids=("cond.synthetic",),
        diagnostic_ids=(),
        groups=(ConditionGroupSpec("group.synthetic", "CORE", "ALL", ("cond.synthetic",)),),
        provision_ids=("provision.synthetic",),
        span_ids=(),
        search_text="orchard blossom",
        maturity="L1",
    )
    cases = [
        CaseDocument(
            case_id=case_id,
            title="Synthetic case",
            search_text=text,
            cited_provision_ids=("provision.synthetic",),
            applied_rule_ids=(),
            invoked_rule_ids=(),
            assessments=(),
            claims=(),
            decisions=(),
            reasoning=(),
            span_ids=(),
            finding_predicates=(),
            finding_ids=(),
        )
        for case_id, text in (
            ("case.a", "distant geology"),
            ("case.z", "orchard blossom"),
            ("case.graph", "rural boundary"),
        )
    ]
    return RetrievalCatalog(
        release_id="synthetic-release",
        index_version="synthetic-index",
        embedding_model="none",
        rules={rule.rule_id: rule},
        cases={case.case_id: case for case in cases},
        conditions={
            "cond.synthetic": ConditionCard(
                "cond.synthetic", "synthetic", "synthetic proposition", (), ()
            )
        },
        rule_ids_by_condition={"cond.synthetic": (rule.rule_id,)},
        provision_text={"provision.synthetic": "synthetic source"},
    )


class _Search:
    def __init__(self, ids=("case.z", "case.a")):
        self.ids = ids
        self.calls = []

    def search(self, text, *, target, context, top_k):
        self.calls.append((target, top_k))
        return [
            scored_reference(key, 1.0 / rank, channels=["bm25"], components=[], reasons=[])
            for rank, key in enumerate(self.ids[:top_k], 1)
        ]


class _Graph(GraphNavigator):
    def __init__(self, channel, ids):
        self.channel, self.ids = channel, ids

    def neighbor_rules(self, context, rule_ids):
        return []

    def _hits(self, channel):
        if self.channel not in {channel, "all"}:
            return []
        return [
            GraphHit(
                case_id=case_id,
                condition_id="cond.synthetic" if channel == "graph" else None,
                rule_id="rule.synthetic" if channel == "applies" else None,
                status="UNKNOWN" if channel == "graph" else None,
                finding_ids=(),
                span_ids=(),
                path_nodes=(case_id, "provision.synthetic"),
                path_edges=("CITES_PROVISION",),
            )
            for case_id in self.ids
        ]

    def cases_by_conditions(self, context, condition_ids):
        return self._hits("graph")

    def cases_applying_rules(self, context, rule_ids):
        return self._hits("applies")

    def cases_citing_provisions(self, context, rule_ids):
        return self._hits("cites")


def _run_case(
    channel, graph_ids, *, text_ids=("case.z", "case.a"), catalog=None, state=None,
    settings=None,
):
    catalog = catalog or _catalog()
    navigator = _Graph(channel, graph_ids)
    search = _Search(text_ids)
    retriever = HybridCypherCaseRetriever(
        navigator, search,
        settings or HybridCypherSettings(case_recall_top_k=3, case_rerank_pool_size=3),
        catalog=catalog,
    )
    context_token = bind_context(catalog.context())
    round_token, scope_token = bind_round(None), bind_scope(None)
    try:
        with capture_retrieval_rankings() as trace:
            bundle = retriever.retrieve(
                state or QueryState(session_id="synthetic", initial_query="orchard blossom"),
                [scored_reference("rule.synthetic", 1.0, channels=["bm25"], components=[], reasons=[])],
            )
        return trace, bundle, current_round()
    finally:
        reset_scope(scope_token)
        reset_round(round_token)
        reset_context(context_token)


@pytest.mark.parametrize("channel", ["graph", "applies", "cites"])
def test_c_unordered_graph_rows_do_not_change_rankings_or_scores(channel):
    forward, _, _ = _run_case(channel, ("case.a", "case.z", "case.graph"))
    reverse, _, _ = _run_case(channel, ("case.graph", "case.z", "case.a"))
    assert forward.ids("c.fused") == reverse.ids("c.fused")
    assert forward.rankings["c.fused"].scores == pytest.approx(
        reverse.rankings["c.fused"].scores
    )


@pytest.mark.parametrize("channel", ["graph", "applies", "cites"])
def test_c_duplicate_graph_paths_do_not_manufacture_extra_relevance(channel):
    single, _, _ = _run_case(channel, ("case.a", "case.z", "case.graph"))
    duplicate, _, _ = _run_case(channel, ("case.a", "case.a", "case.z", "case.graph"))
    assert single.ids("c.fused") == duplicate.ids("c.fused")
    assert single.rankings["c.fused"].scores == pytest.approx(
        duplicate.rankings["c.fused"].scores
    )


@pytest.mark.parametrize("channel", ["graph", "cites"])
def test_c_unknown_broad_graph_link_does_not_displace_direct_text_head(channel):
    trace, _, _ = _run_case(channel, ("case.a", "case.graph"))
    assert trace.ids("c.fused")[0] == "case.z"


@pytest.mark.parametrize("channel", ["graph", "applies", "cites"])
def test_c_graph_only_candidates_are_preserved_without_inventing_roles(channel):
    trace, bundle, evidence = _run_case(channel, ("case.graph",))
    assert "case.graph" in trace.ids("c.fused")
    assert "case.graph" in trace.ids("c.pool")
    assert "case.graph" in evidence.fused_case_ids
    assert "case.graph" in bundle.undetermined_case_ids
    assert bundle.support_case_refs == []
    assert bundle.limiting_case_refs == []
    assert bundle.boundary_case_refs == []


def test_c_without_graph_preserves_direct_text_rank_and_rrf_scores():
    trace, _, _ = _run_case("none", ())
    assert trace.ids("c.fused") == ("case.z", "case.a")
    assert trace.rankings["c.fused"].scores == pytest.approx((1 / 61, 1 / 62))


@pytest.mark.parametrize("channel", ["graph", "applies", "cites", "all"])
def test_c_direct_text_scores_do_not_gain_duplicate_graph_votes(channel):
    baseline, _, _ = _run_case("none", ())
    with_graph, _, _ = _run_case(channel, ("case.a", "case.z", "case.graph"))
    baseline_scores = dict(zip(
        baseline.ids("c.fused"), baseline.rankings["c.fused"].scores, strict=True
    ))
    graph_scores = dict(zip(
        with_graph.ids("c.fused"), with_graph.rankings["c.fused"].scores, strict=True
    ))
    for case_id, score in baseline_scores.items():
        assert graph_scores[case_id] == pytest.approx(score)
    assert 0 < graph_scores["case.graph"] < min(baseline_scores.values())


def test_c_pure_graph_recall_survives_without_query_text_hits():
    first, bundle, _ = _run_case("graph", ("case.z", "case.a"), text_ids=())
    reverse, _, _ = _run_case("graph", ("case.a", "case.z"), text_ids=())
    assert first.ids("c.channel.bm25") == ()
    assert set(first.ids("c.fused")) == {"case.z", "case.a"}
    assert first.ids("c.fused") == reverse.ids("c.fused")
    assert first.rankings["c.fused"].scores == pytest.approx(
        reverse.rankings["c.fused"].scores
    )
    assert all(score > 0 for score in first.rankings["c.fused"].scores)
    assert bundle.fused_case_count == 2


def test_c_correlated_graph_channels_do_not_accumulate_three_independent_votes():
    single_channel_scores = []
    for channel in ("graph", "applies", "cites"):
        trace, _, _ = _run_case(channel, ("case.graph",), text_ids=("case.z",))
        scores = dict(zip(trace.ids("c.fused"), trace.rankings["c.fused"].scores, strict=True))
        single_channel_scores.append(scores["case.graph"])
    combined, _, _ = _run_case("all", ("case.graph",), text_ids=("case.z",))
    scores = dict(zip(combined.ids("c.fused"), combined.rankings["c.fused"].scores, strict=True))
    assert scores["case.graph"] == pytest.approx(max(single_channel_scores))


def test_c_catalog_insertion_order_is_not_a_relevance_signal():
    catalog = _catalog()
    reordered = replace(catalog, cases=dict(reversed(tuple(catalog.cases.items()))))
    original, _, _ = _run_case("cites", tuple(catalog.cases), catalog=catalog)
    changed, _, _ = _run_case("cites", tuple(reordered.cases), catalog=reordered)
    assert original.ids("c.fused") == changed.ids("c.fused")
    assert original.rankings["c.fused"].scores == pytest.approx(
        changed.rankings["c.fused"].scores
    )


def test_c_graph_candidate_budget_is_independent_of_database_row_order():
    settings = HybridCypherSettings(
        case_recall_top_k=3, case_rerank_pool_size=3, per_condition_limit=1
    )
    forward, _, _ = _run_case(
        "graph", ("case.a", "case.z", "case.graph"), text_ids=(), settings=settings
    )
    reverse, _, _ = _run_case(
        "graph", ("case.graph", "case.z", "case.a"), text_ids=(), settings=settings
    )
    assert len(forward.ids("c.fused")) == len(reverse.ids("c.fused")) == 1
    assert forward.ids("c.fused") == reverse.ids("c.fused")


def test_c_graph_only_can_still_qualify_for_a_role_when_evidence_matches():
    catalog = _catalog()
    case_id = "case.graph"
    catalog.cases[case_id] = replace(
        catalog.cases[case_id],
        applied_rule_ids=("rule.synthetic",),
        assessments=(
            CaseAssessment(
                case_id=case_id,
                condition_id="cond.synthetic",
                status=ConditionStatus.SATISFIED,
                finding_ids=("finding.synthetic",),
                span_ids=("span.synthetic",),
            ),
        ),
        span_ids=("span.synthetic",),
        finding_ids=("finding.synthetic",),
    )
    state = QueryState(
        session_id="synthetic-role",
        initial_query="orchard blossom",
        condition_states=[
            QueryConditionState(
                condition_id="cond.synthetic", status=ConditionStatus.SATISFIED
            )
        ],
    )
    trace, bundle, _ = _run_case("applies", (case_id,), catalog=catalog, state=state)
    assert case_id not in trace.ids("c.channel.bm25")
    assert case_id in trace.ids("c.fused")
    assert case_id in trace.ids("c.pool")
    assert [item.object_id for item in bundle.support_case_refs] == [case_id]


@pytest.mark.parametrize(
    "catalog_size,output_limit,recall_limit,expected_budget",
    [(7, 2, 5, 5), (7, 5, 2, 5), (3, 2, 50, 3)],
)
def test_a_candidate_budget_is_separate_from_bounded_final_output(
    catalog_size, output_limit, recall_limit, expected_budget
):
    catalog = _catalog()
    template = catalog.rules["rule.synthetic"]
    rules = {
        f"rule.synthetic.{i}": replace(template, rule_id=f"rule.synthetic.{i}")
        for i in range(catalog_size)
    }
    catalog = replace(
        catalog, rules=rules, rule_ids_by_condition={"cond.synthetic": tuple(rules)}
    )
    search = _Search(tuple(reversed(tuple(rules))))
    navigator = _Graph("none", ())
    retriever = HybridCypherRuleRetriever(
        navigator, search,
        HybridCypherSettings(rule_top_k=output_limit, rule_recall_top_k=recall_limit),
        catalog=catalog,
    )
    context_token, scope_token = bind_context(None), bind_scope(None)
    try:
        with capture_retrieval_rankings() as trace:
            result = retriever.retrieve(
                QueryState(session_id="synthetic-budget", initial_query="orchard blossom")
            )
        assert search.calls == [("rule", expected_budget)]
        assert len(trace.ids("a.channel.bm25")) == expected_budget
        assert len(result) == min(output_limit, catalog_size)
        assert tuple(ref.object_id for ref in result) == trace.ids("a.final")
    finally:
        reset_scope(scope_token)
        reset_context(context_token)


@pytest.mark.parametrize("invalid", [0, -1])
def test_a_recall_budget_must_be_positive(invalid):
    with pytest.raises(ValueError):
        HybridCypherSettings(rule_recall_top_k=invalid)
