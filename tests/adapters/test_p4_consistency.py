"""P4 一致性约束的确定性反例（固定替身，不调用线上模型）。"""

from __future__ import annotations

from typing import ClassVar

from casepath.adapters.conditions.case_condition_evidence import aggregate_case_condition
from casepath.adapters.conditions.semantic_answer_interpreter import SemanticAnswerInterpreter
from casepath.adapters.conditions.semantic_condition_projector import SemanticConditionProjector
from casepath.adapters.questioning.key_condition_policy import KeyConditionQuestionPolicy
from casepath.adapters.retrieval.retrieval_catalog import (
    CaseAssessment,
    RetrievalContext,
    RoundEvidence,
    RuleScope,
    bind_round,
    bind_scope,
    reset_round,
    reset_scope,
)
from casepath.application.runtime_meta import (
    SessionRuntimeMeta,
    bind_runtime_meta,
    reset_runtime_meta,
)
from casepath.contracts import (
    AnswerRequest,
    ComparisonBundle,
    ConditionStatus,
    QueryConditionState,
    QueryState,
    QuestionCandidate,
    RetrievalBundle,
    ScoreComponent,
    ScoredReference,
    UserFact,
)


def _ref(rule_id: str) -> ScoredReference:
    return ScoredReference(
        object_id=rule_id,
        score=1.0,
        retrieval_channels=["bm25"],
        score_components=[ScoreComponent(name="rrf", value=1.0, explanation="t")],
        reasons=["t"],
    )


def test_unable_answer_does_not_change_existing_condition():
    state = QueryState(
        session_id="s-unable",
        initial_query="培训班关门了能不能退费",
        condition_states=[
            QueryConditionState(
                condition_id="cond.contract_terminated",
                status=ConditionStatus.UNKNOWN,
                mapping_reasons=["初始未知"],
            )
        ],
        user_facts=[
            UserFact(fact_id="fact.initial.query", text="培训班关门了能不能退费", source_turn=0)
        ],
    )
    pending = QuestionCandidate(
        question_id="question.fact.cond.contract_terminated.1",
        condition_id="cond.contract_terminated",
        question="双方是否已经同意结束培训合同？",
        why_asked="核对解除沟通进展",
        options=["不清楚", "暂不回答"],
        utility=0.5,
    )
    interpretation = SemanticAnswerInterpreter().interpret(
        state,
        pending,
        AnswerRequest(
            question_id=pending.question_id,
            condition_id=pending.condition_id,
            answer="不清楚",
            selected_option="不清楚",
        ),
    )
    assert interpretation.disposition == "UNABLE"
    assert interpretation.condition_updates == []
    assert interpretation.unable_condition_ids == ["cond.contract_terminated"]


def test_background_rule_does_not_generate_questions():
    scope = RuleScope(
        active_rule_ids=(),
        alternative_rule_ids=(),
        background_rule_ids=("rule.background",),
        active_condition_ids=(),
    )
    token = bind_scope(scope)
    try:
        policy = KeyConditionQuestionPolicy(catalog=_EmptyCatalog(), language_model=None)
        state = QueryState(session_id="s-bg", initial_query="测试")
        bundle = RetrievalBundle(rule_refs=[_ref("rule.background")])
        question = policy.select(state, bundle, ComparisonBundle())
        assert question is None
    finally:
        reset_scope(token)


class _EmptyCatalog:
    rules: ClassVar[dict] = {}
    conditions: ClassVar[dict] = {}


def test_case_condition_aggregation_is_shared_by_c_and_d():
    assessments = (
        CaseAssessment(
            case_id="c1",
            condition_id="cond.x",
            status=ConditionStatus.SATISFIED,
            finding_ids=("f1",),
            span_ids=("s1",),
        ),
        CaseAssessment(
            case_id="c1",
            condition_id="cond.x",
            status=ConditionStatus.NOT_SATISFIED,
            finding_ids=("f2",),
            span_ids=("s2",),
        ),
    )
    first = aggregate_case_condition(case_id="c1", condition_id="cond.x", assessments=assessments)
    second = aggregate_case_condition(case_id="c1", condition_id="cond.x", assessments=assessments)
    assert first == second
    assert first.conflicting is True
    assert first.status is ConditionStatus.CONFLICTING


def test_undetermined_case_remains_available_for_comparison():
    context = RetrievalContext("rel", "idx", "none")
    evidence = RoundEvidence(
        context=context,
        assessments_by_case={
            "case.u": (
                CaseAssessment(
                    case_id="case.u",
                    condition_id="cond.payment_made",
                    status=ConditionStatus.SATISFIED,
                    finding_ids=("f",),
                    span_ids=("s",),
                ),
            )
        },
        paths_by_case={},
        graph_only_ids=frozenset(),
        fused_case_ids=("case.u",),
        undetermined_case_ids=frozenset({"case.u"}),
    )
    token = bind_round(evidence)
    try:
        from casepath.adapters.conditions.condition_comparator import _collect_comparison_cases

        ids = _collect_comparison_cases(RetrievalBundle(), evidence)
        assert "case.u" in ids
    finally:
        reset_round(token)


def test_newly_activated_condition_uses_historical_facts():
    from adapters.test_p4_hybrid_cypher import fixture_catalog

    catalog = fixture_catalog()
    rule_id = next(iter(catalog.rules))
    condition_ids = tuple(catalog.rules[rule_id].condition_ids)
    meta = SessionRuntimeMeta()
    meta_token = bind_runtime_meta(meta)
    scope_token = bind_scope(
        RuleScope(
            active_rule_ids=(rule_id,),
            alternative_rule_ids=(),
            background_rule_ids=(),
            active_condition_ids=condition_ids,
        )
    )
    try:
        state = QueryState(
            session_id="s-hist",
            initial_query="我付了钱办了卡",
            user_facts=[
                UserFact(fact_id="fact.initial.query", text="我付了钱办了卡", source_turn=0),
                UserFact(fact_id="fact.pay", text="已经付款了", source_turn=0),
            ],
            condition_states=[],
        )
        projected = SemanticConditionProjector(catalog, language_model=None).project(
            state, [_ref(rule_id)]
        )
        assert projected.condition_states
        assert meta.projection_ledger
    finally:
        reset_scope(scope_token)
        reset_runtime_meta(meta_token)
