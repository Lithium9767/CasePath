"""验收缺口修复：上下文生命周期、纠正链、预算与去重控制逻辑。"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import ClassVar

import pytest
from pydantic import ValidationError

from casepath.adapters.conditions.semantic_answer_interpreter import SemanticAnswerInterpreter
from casepath.adapters.questioning.key_condition_policy import (
    CallBudget,
    KeyConditionQuestionPolicy,
    QuestionDraft,
    _check_missing_fact_against_history,
)
from casepath.adapters.questioning.missing_facts import (
    MissingFactDraft,
    MissingFactIdentifyBatch,
)
from casepath.adapters.retrieval.retrieval_catalog import (
    RetrievalContext,
    RoundEvidence,
    RuleScope,
    bind_round,
    bind_scope,
    current_round,
    current_scope,
    reset_round,
    reset_scope,
    set_round,
)
from casepath.adapters.runtime.request_context import p4_request_context
from casepath.application.errors import InvalidComponentOutput, QuestionGenerationFailed
from casepath.application.runtime_meta import (
    CorrectionLink,
    SessionRuntimeMeta,
    current_runtime_meta,
)
from casepath.application.session_service import SessionService
from casepath.contracts import (
    AnswerInterpretation,
    AnswerRequest,
    ComparisonBundle,
    ConditionEvidence,
    ConditionStatus,
    CorrectionOperation,
    DialogueTurn,
    QueryConditionState,
    QueryState,
    QuestionCandidate,
    RetrievalBundle,
    ScoreComponent,
    ScoredReference,
    UserFact,
    WorkflowSnapshot,
)


def _empty_round(label: str = "t") -> RoundEvidence:
    return RoundEvidence(
        context=RetrievalContext(release_id=label, index_version="i", embedding_model="m"),
        assessments_by_case={},
        paths_by_case={},
        graph_only_ids=frozenset(),
        fused_case_ids=(f"case.{label}",),
    )


def test_request_context_restores_round_after_success():
    outer = _empty_round("outer")
    token = bind_round(outer)
    try:
        with p4_request_context(SessionRuntimeMeta()):
            set_round(_empty_round("inner"))
            assert current_round().fused_case_ids == ("case.inner",)
        assert current_round() is outer
    finally:
        reset_round(token)


def test_request_context_restores_round_after_exception():
    with p4_request_context(SessionRuntimeMeta()):
        set_round(_empty_round("boom"))
        try:
            raise RuntimeError("C failed")
        except RuntimeError:
            pass
    assert current_round() is None


def test_consecutive_runs_do_not_see_prior_round_evidence():
    with p4_request_context(SessionRuntimeMeta()):
        set_round(_empty_round("r1"))
        assert current_round().fused_case_ids == ("case.r1",)
    with p4_request_context(SessionRuntimeMeta()):
        assert current_round() is None
        set_round(_empty_round("r2"))
        assert current_round().fused_case_ids == ("case.r2",)
    assert current_round() is None


def test_nested_scope_restores_outer_value():
    outer = RuleScope(("rule.a",), (), (), ("cond.a",))
    outer_token = bind_scope(outer)
    try:
        inner_token = bind_scope(RuleScope(("rule.b",), (), (), ("cond.b",)))
        try:
            assert current_scope().active_rule_ids == ("rule.b",)
        finally:
            reset_scope(inner_token)
        assert current_scope() is outer
    finally:
        reset_scope(outer_token)
    assert current_scope() is None


def test_concurrent_requests_do_not_pollute_round():
    barriers = threading.Barrier(2)
    results: dict[str, tuple[str, ...]] = {}

    def worker(name: str) -> None:
        with p4_request_context(SessionRuntimeMeta()):
            set_round(_empty_round(name))
            barriers.wait()
            results[name] = current_round().fused_case_ids
            barriers.wait()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, "a"), pool.submit(worker, "b")]
        for future in futures:
            future.result()
    assert results["a"] == ("case.a",)
    assert results["b"] == ("case.b",)
    assert current_round() is None


def test_explicit_amount_correction_does_not_invalidate_unrelated_mappings():
    state = QueryState(
        session_id="s-corr",
        initial_query="我交了5000元，已经上了10节课，店关门了。",
        user_facts=[
            UserFact(
                fact_id="fact.initial.query",
                text="我交了5000元，已经上了10节课，店关门了。",
                source_turn=0,
            )
        ],
        condition_states=[
            QueryConditionState(
                condition_id="cond.payment_made",
                status=ConditionStatus.SATISFIED,
                supporting_fact_ids=["fact.initial.query"],
                evidence=[
                    ConditionEvidence(
                        fact_id="fact.initial.query",
                        relation="SUPPORTS",
                        confidence=0.8,
                        reason="金额原话",
                    )
                ],
                mapping_reasons=["初始映射"],
            ),
            QueryConditionState(
                condition_id="cond.performance_impossible",
                status=ConditionStatus.SATISFIED,
                supporting_fact_ids=["fact.initial.query"],
                evidence=[
                    ConditionEvidence(
                        fact_id="fact.initial.query",
                        relation="SUPPORTS",
                        confidence=0.8,
                        reason="关门原话",
                    )
                ],
                mapping_reasons=["初始映射"],
            ),
        ],
    )
    pending = QuestionCandidate(
        question_id="question.fact.cond.payment_made.1",
        condition_id="cond.payment_made",
        question="实际支付金额是多少？",
        why_asked="核对金额",
        options=["不清楚", "暂不回答"],
        utility=0.5,
    )
    interpretation = SemanticAnswerInterpreter().interpret(
        state,
        pending,
        AnswerRequest(
            question_id=pending.question_id,
            condition_id=pending.condition_id,
            answer="金额说错了，是6000元。",
        ),
    )
    assert interpretation.corrections
    assert interpretation.corrections[0].target_fact_id == "fact.initial.query"
    assert interpretation.corrections[0].condition_id == "cond.payment_made"
    # 纠正只绑定支付条件，不要求整段段落事实失效。
    assert interpretation.corrections[0].condition_id != "cond.performance_impossible"


def test_ordinary_conflict_is_not_auto_correction():
    state = QueryState(
        session_id="s-conflict",
        initial_query="我交了5000元",
        user_facts=[UserFact(fact_id="fact.initial.query", text="我交了5000元", source_turn=0)],
        condition_states=[
            QueryConditionState(
                condition_id="cond.payment_made",
                status=ConditionStatus.SATISFIED,
                supporting_fact_ids=["fact.initial.query"],
                evidence=[
                    ConditionEvidence(
                        fact_id="fact.initial.query",
                        relation="SUPPORTS",
                        confidence=0.8,
                        reason="金额",
                    )
                ],
                mapping_reasons=["初始"],
            )
        ],
    )
    pending = QuestionCandidate(
        question_id="question.fact.cond.payment_made.1",
        condition_id="cond.payment_made",
        question="支付金额？",
        why_asked="核对",
        options=["不清楚", "暂不回答"],
        utility=0.5,
    )
    interpretation = SemanticAnswerInterpreter().interpret(
        state,
        pending,
        AnswerRequest(
            question_id=pending.question_id,
            condition_id=pending.condition_id,
            answer="我交的是6000元。",
        ),
    )
    assert interpretation.corrections == []


def test_amount_correction_does_not_target_unrelated_numeric_fact():
    """纠正金额不得仅因“含数字”误选课时等无关事实。"""
    state = QueryState(
        session_id="s-numeric",
        initial_query="培训退费",
        user_facts=[
            UserFact(fact_id="fact.pay", text="我交了5000元。", source_turn=0),
            UserFact(fact_id="fact.lessons", text="已经上了10节课。", source_turn=1),
        ],
        dialogue_history=[
            DialogueTurn(
                turn_id=1,
                question_id="q.lessons",
                condition_id="cond.payment_made",
                question="上了几节课？",
                answer="已经上了10节课。",
            )
        ],
        condition_states=[
            QueryConditionState(
                condition_id="cond.payment_made",
                status=ConditionStatus.SATISFIED,
                supporting_fact_ids=["fact.pay"],
                evidence=[
                    ConditionEvidence(
                        fact_id="fact.pay",
                        relation="SUPPORTS",
                        confidence=0.8,
                        reason="付款原话",
                    )
                ],
                mapping_reasons=["初始"],
            )
        ],
    )
    pending = QuestionCandidate(
        question_id="question.fact.cond.payment_made.1",
        condition_id="cond.payment_made",
        question="实际支付金额是多少？",
        why_asked="核对金额",
        options=["不清楚", "暂不回答"],
        utility=0.5,
    )
    interpretation = SemanticAnswerInterpreter().interpret(
        state,
        pending,
        AnswerRequest(
            question_id=pending.question_id,
            condition_id=pending.condition_id,
            answer="金额说错了，是6000元。",
        ),
    )
    assert len(interpretation.corrections) == 1
    assert interpretation.corrections[0].target_fact_id == "fact.pay"
    assert interpretation.corrections[0].target_fact_id != "fact.lessons"


def test_amount_correction_binds_to_payment_not_pending_question():
    state = QueryState(
        session_id="s-bind",
        initial_query="我交了5000元培训费，店关门了。",
        user_facts=[
            UserFact(
                fact_id="fact.initial.query",
                text="我交了5000元培训费，店关门了。",
                source_turn=0,
            )
        ],
        condition_states=[
            QueryConditionState(
                condition_id="cond.payment_made",
                status=ConditionStatus.SATISFIED,
                supporting_fact_ids=["fact.initial.query"],
                evidence=[
                    ConditionEvidence(
                        fact_id="fact.initial.query",
                        relation="SUPPORTS",
                        confidence=0.8,
                        reason="金额",
                    )
                ],
                mapping_reasons=["初始"],
            ),
            QueryConditionState(
                condition_id="cond.contract_exists",
                status=ConditionStatus.UNKNOWN,
                mapping_reasons=["初始"],
            ),
        ],
    )
    from casepath.adapters.conditions.semantic_answer_interpreter import _explicit_correction

    replacement = UserFact(
        fact_id="fact.answer.1.cond.contract_exists",
        text="金额说错了，是6000元。",
        source_turn=1,
    )
    ops = _explicit_correction(
        state,
        answer=replacement.text,
        replacement=replacement,
        condition_id="cond.contract_exists",
    )
    assert len(ops) == 1
    assert ops[0].condition_id == "cond.payment_made"
    assert ops[0].target_fact_id == "fact.initial.query"


def test_amount_correction_ignores_recent_numeric_fact_without_money_cue():
    """即使最近事实含数字，无金钱线索且未映射到本条件时不得作为纠正目标。"""
    state = QueryState(
        session_id="s-recent-num",
        initial_query="退费",
        user_facts=[
            UserFact(fact_id="fact.pay", text="我交了5000元。", source_turn=0),
            UserFact(fact_id="fact.term", text="合同写了12个月。", source_turn=1),
        ],
        dialogue_history=[
            DialogueTurn(
                turn_id=1,
                question_id="q.term",
                condition_id="cond.payment_made",
                question="合同期限？",
                answer="合同写了12个月。",
            )
        ],
        condition_states=[
            QueryConditionState(
                condition_id="cond.payment_made",
                status=ConditionStatus.SATISFIED,
                supporting_fact_ids=["fact.pay"],
                evidence=[
                    ConditionEvidence(
                        fact_id="fact.pay",
                        relation="SUPPORTS",
                        confidence=0.8,
                        reason="付款原话",
                    )
                ],
                mapping_reasons=["初始"],
            )
        ],
    )
    interpretation = SemanticAnswerInterpreter().interpret(
        state,
        QuestionCandidate(
            question_id="question.fact.cond.payment_made.1",
            condition_id="cond.payment_made",
            question="金额？",
            why_asked="核对",
            options=["不清楚", "暂不回答"],
            utility=0.5,
        ),
        AnswerRequest(
            question_id="question.fact.cond.payment_made.1",
            condition_id="cond.payment_made",
            answer="金额说错了，是6000元。",
        ),
    )
    assert interpretation.corrections[0].target_fact_id == "fact.pay"


def test_llm_rejects_pairs_outside_delta_pairs():
    """拆成独立事实/条件列表后，模型不得越界重判未列入 delta 的旧配对。"""
    from casepath.adapters.conditions.semantic_condition_projector import (
        ConditionLinkBatch,
        ConditionLinkDraft,
        SemanticConditionProjector,
    )
    from casepath.adapters.retrieval.retrieval_catalog import (
        ConditionCard,
        ConditionGroupSpec,
        RetrievalCatalog,
        RuleDocument,
        RuleScope,
        bind_scope,
        reset_scope,
    )
    from casepath.application.runtime_meta import (
        MAPPER_VERSION,
        ProjectionLedgerEntry,
        SessionRuntimeMeta,
        bind_runtime_meta,
        condition_definition_version,
        fact_text_version,
        reset_runtime_meta,
    )
    from casepath.contracts import ScoredReference

    catalog = RetrievalCatalog(
        release_id="pair-test",
        index_version="i",
        embedding_model="none",
        rules={
            "rule.map.v1": RuleDocument(
                rule_id="rule.map.v1",
                title="映射测试",
                claim_types=("服务合同解除与返还",),
                condition_ids=("cond.payment_made", "cond.performance_impossible"),
                core_ids=("cond.payment_made", "cond.performance_impossible"),
                diagnostic_ids=(),
                groups=(
                    ConditionGroupSpec(
                        "core",
                        "CORE",
                        "ALL",
                        ("cond.payment_made", "cond.performance_impossible"),
                    ),
                ),
                provision_ids=(),
                span_ids=(),
                search_text="映射",
                maturity="L1",
            )
        },
        cases={},
        conditions={
            "cond.payment_made": ConditionCard(
                "cond.payment_made", "已付款", "已付款", (), ()
            ),
            "cond.performance_impossible": ConditionCard(
                "cond.performance_impossible", "履行不能", "履行不能", (), ()
            ),
        },
        rule_ids_by_condition={
            "cond.payment_made": ("rule.map.v1",),
            "cond.performance_impossible": ("rule.map.v1",),
        },
        provision_text={},
    )
    pay_card = catalog.conditions["cond.payment_made"]
    meta = SessionRuntimeMeta(
        projection_ledger=[
            ProjectionLedgerEntry(
                fact_id="fact.initial.query",
                condition_id="cond.payment_made",
                condition_definition_version=condition_definition_version(
                    pay_card.predicate, pay_card.label
                ),
                mapper_version=MAPPER_VERSION,
                fact_text_version=fact_text_version("我交了5000元"),
                outcome="MAPPED",
            )
        ]
    )

    class OverreachModel:
        def generate_structured(self, **kwargs):
            payload = json.loads(kwargs["user_prompt"])
            assert "allowed_pairs" in payload
            # 故意越界：改写已入账的 (fact.initial, payment)
            return ConditionLinkBatch(
                links=[
                    ConditionLinkDraft(
                        fact_id="fact.initial.query",
                        condition_id="cond.payment_made",
                        relation="OPPOSES",
                        quoted_span="交了5000元",
                        rationale="越界重判旧配对",
                        raw_confidence=0.99,
                    ),
                    ConditionLinkDraft(
                        fact_id="fact.answer.1.cond.performance_impossible",
                        condition_id="cond.performance_impossible",
                        relation="SUPPORTS",
                        quoted_span="店关门了",
                        rationale="合法新配对",
                        raw_confidence=0.9,
                    ),
                ]
            )

    meta_token = bind_runtime_meta(meta)
    scope_token = bind_scope(
        RuleScope(
            active_rule_ids=("rule.map.v1",),
            alternative_rule_ids=(),
            background_rule_ids=(),
            active_condition_ids=("cond.payment_made", "cond.performance_impossible"),
        )
    )
    try:
        state = QueryState(
            session_id="s-pair",
            initial_query="我交了5000元",
            user_facts=[
                UserFact(fact_id="fact.initial.query", text="我交了5000元", source_turn=0),
                UserFact(
                    fact_id="fact.answer.1.cond.performance_impossible",
                    text="店关门了",
                    source_turn=1,
                ),
            ],
            dialogue_history=[
                DialogueTurn(
                    turn_id=1,
                    question_id="q.close",
                    condition_id="cond.performance_impossible",
                    question="店是否关门？",
                    answer="店关门了",
                )
            ],
            condition_states=[
                QueryConditionState(
                    condition_id="cond.payment_made",
                    status=ConditionStatus.SATISFIED,
                    supporting_fact_ids=["fact.initial.query"],
                    evidence=[
                        ConditionEvidence(
                            fact_id="fact.initial.query",
                            relation="SUPPORTS",
                            confidence=0.8,
                            reason="历史付款映射",
                        )
                    ],
                    mapping_reasons=["历史"],
                ),
                QueryConditionState(
                    condition_id="cond.performance_impossible",
                    status=ConditionStatus.UNKNOWN,
                    mapping_reasons=["初始"],
                ),
            ],
        )
        projected = SemanticConditionProjector(catalog, OverreachModel()).project(
            state,
            [ScoredReference(object_id="rule.map.v1", score=1.0, reasons=["t"],
                             retrieval_channels=["bm25"],
                             score_components=[ScoreComponent(name="rrf", value=1.0, explanation="t")])],
        )
        payment = next(
            item for item in projected.condition_states if item.condition_id == "cond.payment_made"
        )
        impossible = next(
            item
            for item in projected.condition_states
            if item.condition_id == "cond.performance_impossible"
        )
        # 旧付款映射不得被越界 OPPOSES 覆盖
        assert payment.status is ConditionStatus.SATISFIED
        assert payment.evidence[0].relation == "SUPPORTS"
        assert impossible.status is ConditionStatus.SATISFIED
    finally:
        reset_scope(scope_token)
        reset_runtime_meta(meta_token)

def test_merge_rejects_unknown_correction_target():
    from casepath.application.errors import InvalidComponentOutput

    state = QueryState(
        session_id="s-bad",
        initial_query="测试",
        condition_states=[
            QueryConditionState(condition_id="cond.payment_made", mapping_reasons=["初始"])
        ],
    )
    with pytest.raises((InvalidComponentOutput, ValueError, ValidationError)):
        SessionService._merge_answer(
            state,
            "问题",
            AnswerRequest(
                question_id="q1",
                condition_id="cond.payment_made",
                answer="金额说错了，是6000元。",
            ),
            AnswerInterpretation(
                new_facts=[
                    UserFact(
                        fact_id="fact.answer.1.cond.payment_made",
                        text="金额说错了，是6000元。",
                        source_turn=1,
                    )
                ],
                corrections=[
                    CorrectionOperation(
                        target_fact_id="fact.missing",
                        replacement_fact_id="fact.answer.1.cond.payment_made",
                        condition_id="cond.payment_made",
                        reason="伪造纠正",
                    )
                ],
            ),
        )


def test_failed_workflow_does_not_commit_corrections():
    class BoomWorkflow:
        def run(self, state):
            raise ValueError("boom")

    class MemoryRepo:
        def __init__(self):
            self.record = None

        def get(self, session_id):
            return self.record

        def create(self, record):
            self.record = record

        def save(self, record, expected_revision):
            self.record = record

    from casepath.application.models import SessionRecord

    repo = MemoryRepo()
    service = SessionService(repo, BoomWorkflow(), SemanticAnswerInterpreter())
    state = QueryState(
        session_id="s-fail",
        initial_query="我交了5000元",
        user_facts=[UserFact(fact_id="fact.initial.query", text="我交了5000元", source_turn=0)],
        condition_states=[
            QueryConditionState(condition_id="cond.payment_made", mapping_reasons=["初始"])
        ],
    )
    snapshot = WorkflowSnapshot(
        query_state=state,
        retrieval_bundle=RetrievalBundle(),
        comparison_bundle=ComparisonBundle(),
        explanation_plan=__import__(
            "casepath.contracts", fromlist=["ExplanationPlan"]
        ).ExplanationPlan(
            session_id="s-fail",
            main_explanation="占位解释文本用于失败提交测试。",
        ),
        next_question=QuestionCandidate(
            question_id="question.fact.cond.payment_made.1",
            condition_id="cond.payment_made",
            question="金额？",
            why_asked="核对金额",
            options=["不清楚", "暂不回答"],
            utility=0.5,
        ),
        trace=["INIT"],
    )
    repo.create(
        SessionRecord(
            session_id="s-fail",
            latest_snapshot=snapshot,
            runtime_meta=SessionRuntimeMeta(),
        )
    )
    with pytest.raises(InvalidComponentOutput):
        service.submit_answer(
            "s-fail",
            AnswerRequest(
                question_id="question.fact.cond.payment_made.1",
                condition_id="cond.payment_made",
                answer="金额说错了，是6000元。",
            ),
        )
    assert repo.record.runtime_meta.corrections == []


def test_budget_counts_format_retry_and_askable_false():
    class FlakyModel:
        def __init__(self):
            self.calls = 0

        def generate_structured(self, **kwargs):
            self.calls += 1
            stage = kwargs.get("stage")
            if stage == "missing_facts":
                if self.calls == 1:
                    from casepath.application.errors import InvalidComponentOutput

                    raise InvalidComponentOutput("schema_invalid")
                return MissingFactIdentifyBatch(
                    items=[
                        MissingFactDraft(
                            condition_ids=["cond.payment_made"],
                            description="是否已付款",
                            status="MISSING",
                            why_needed="核心条件",
                        )
                    ]
                )
            return QuestionDraft(
                condition_id="cond.payment_made",
                askable=False,
                question="",
                reason="历史事实已覆盖",
            )

    class Catalog:
        rules: ClassVar[dict] = {
            "rule.x": type(
                "R",
                (),
                {
                    "groups": [
                        type(
                            "G",
                            (),
                            {
                                "role": "CORE",
                                "operator": "ALL",
                                "member_ids": ["cond.payment_made"],
                            },
                        )()
                    ],
                    "condition_ids": ["cond.payment_made"],
                    "title": "测试规则",
                },
            )()
        }
        conditions: ClassVar[dict] = {
            "cond.payment_made": type(
                "C",
                (),
                {"predicate": "已付款", "label": "付款", "user_answerable": True},
            )()
        }

    model = FlakyModel()
    policy = KeyConditionQuestionPolicy(Catalog(), language_model=model, generation_budget=8)
    token = bind_scope(
        RuleScope(("rule.x",), (), (), ("cond.payment_made",))
    )
    meta_token = None
    from casepath.application.runtime_meta import bind_runtime_meta, reset_runtime_meta

    meta = SessionRuntimeMeta()
    meta_token = bind_runtime_meta(meta)
    try:
        state = QueryState(
            session_id="s-budget",
            initial_query="测试",
            condition_states=[
                QueryConditionState(
                    condition_id="cond.payment_made",
                    status=ConditionStatus.UNKNOWN,
                    mapping_reasons=["初始"],
                )
            ],
        )
        question = policy.select(
            state,
            RetrievalBundle(
                rule_refs=[
                    ScoredReference(
                        object_id="rule.x",
                        score=1.0,
                        retrieval_channels=["bm25"],
                        score_components=[
                            ScoreComponent(name="rrf", value=1.0, explanation="t")
                        ],
                        reasons=["t"],
                    )
                ]
            ),
            ComparisonBundle(),
        )
        assert question is None
        assert model.calls == 3  # identify retry + identify ok + askable=false generate
        assert meta.last_stop_reason in {
            None,
            "NO_KEY_MISSING_FACT",
            "ALL_ALREADY_ANSWERED",
            "USER_UNABLE_TO_ANSWER",
        }
    finally:
        reset_runtime_meta(meta_token)
        reset_scope(token)


def test_budget_exhausted_stops_before_model_call():
    class CountingModel:
        def __init__(self):
            self.calls = 0

        def generate_structured(self, **kwargs):
            self.calls += 1
            return QuestionDraft(
                condition_id="cond.payment_made",
                askable=True,
                question="你付了多少钱？",
                reason="需要确认付款金额以便对照规则条件。",
            )

    budget = CallBudget(0)
    with pytest.raises(QuestionGenerationFailed, match="question_budget_exhausted"):
        if not budget.consume("question_generation"):
            raise QuestionGenerationFailed("question_budget_exhausted")
    assert budget.used == 0


def test_already_answered_checker_blocks_question():
    def stub_checker(**kwargs):
        return "ALREADY_ANSWERED", "stub"

    class Catalog:
        rules: ClassVar[dict] = {
            "rule.x": type(
                "R",
                (),
                {
                    "groups": [
                        type(
                            "G",
                            (),
                            {
                                "role": "CORE",
                                "operator": "ALL",
                                "member_ids": ["cond.children"],
                            },
                        )()
                    ],
                    "condition_ids": ["cond.children"],
                    "title": "继承",
                },
            )()
        }
        conditions: ClassVar[dict] = {
            "cond.children": type(
                "C",
                (),
                {"predicate": "子女情况", "label": "子女", "user_answerable": True},
            )()
        }

    class BoomModel:
        def generate_structured(self, **kwargs):
            raise AssertionError("should not call model when already answered")

    token = bind_scope(RuleScope(("rule.x",), (), (), ("cond.children",)))
    try:
        policy = KeyConditionQuestionPolicy(
            Catalog(), language_model=BoomModel(), fact_checker=stub_checker
        )
        question = policy.select(
            QueryState(
                session_id="s-dedup",
                initial_query="只有我和姐姐",
                condition_states=[
                    QueryConditionState(
                        condition_id="cond.children",
                        status=ConditionStatus.UNKNOWN,
                        mapping_reasons=["初始"],
                    )
                ],
            ),
            RetrievalBundle(
                rule_refs=[
                    ScoredReference(
                        object_id="rule.x",
                        score=1.0,
                        retrieval_channels=["bm25"],
                        score_components=[
                            ScoreComponent(name="rrf", value=1.0, explanation="t")
                        ],
                        reasons=["t"],
                    )
                ]
            ),
            ComparisonBundle(),
        )
        assert question is None
    finally:
        reset_scope(token)


def test_program_checker_marks_answered_dialogue():
    state = QueryState(
        session_id="s",
        initial_query="继承",
        dialogue_history=[
            __import__("casepath.contracts", fromlist=["DialogueTurn"]).DialogueTurn(
                turn_id=1,
                question_id="q1",
                condition_id="cond.children",
                question="有几个子女？",
                answer="只有我和姐姐",
            )
        ],
        condition_states=[
            QueryConditionState(condition_id="cond.children", mapping_reasons=["初始"])
        ],
    )
    outcome, _ = _check_missing_fact_against_history(
        condition_id="cond.children",
        state=state,
        unable=set(),
        declined=set(),
    )
    assert outcome == "ALREADY_ANSWERED"


def test_runtime_meta_correction_link_roundtrip():
    meta = SessionRuntimeMeta(
        corrections=[
            CorrectionLink(
                target_fact_id="fact.a",
                replacement_fact_id="fact.b",
                condition_id="cond.payment_made",
                reason="明确纠正金额",
            )
        ]
    )
    with p4_request_context(meta):
        assert current_runtime_meta().corrections[0].target_fact_id == "fact.a"
    # 恢复到进入包装层之前的值（测试夹具可能已绑定空 meta）。
    restored = current_runtime_meta()
    assert restored is None or restored.corrections == []
