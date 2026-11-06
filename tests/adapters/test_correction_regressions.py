"""纠正失效、目标歧义与非法映射的独立回归。"""

from types import SimpleNamespace

import pytest

from casepath.adapters.conditions.semantic_answer_interpreter import _explicit_correction
from casepath.adapters.conditions.semantic_condition_projector import (
    ConditionLinkBatch,
    ConditionLinkDraft,
    SemanticConditionProjector,
)
from casepath.adapters.retrieval.retrieval_catalog import RuleScope, set_scope
from casepath.adapters.runtime.request_context import p4_request_context
from casepath.application.runtime_meta import CorrectionLink, SessionRuntimeMeta
from casepath.contracts import (
    ConditionEvidence,
    ConditionStatus,
    DialogueTurn,
    QueryConditionState,
    QueryState,
    UserFact,
)


@pytest.mark.parametrize("fact_id,quote,condition", [
    (None, "", "cond.payment_made"),
    ("invented", "交了5000元", "cond.payment_made"),
    (None, "交了5000元", "cond.invented"),
])
def test_unlocatable_rejected_link_does_not_complete_ledger(fact_id, quote, condition):
    class Model:
        def generate_structured(self, **kwargs):
            return ConditionLinkBatch(links=[ConditionLinkDraft(
                fact_id=fact_id, condition_id=condition, quoted_span=quote,
                relation="SUPPORTS", rationale="坏链接", raw_confidence=0.8,
            )])
    catalog = SimpleNamespace(conditions={"cond.payment_made": SimpleNamespace(
        predicate="付款", label="付款",
    )}, rules={})
    meta = SessionRuntimeMeta()
    with p4_request_context(meta):
        set_scope(RuleScope((), (), (), ("cond.payment_made",)))
        SemanticConditionProjector(catalog, Model()).project(_state(), [])
        assert meta.projection_ledger == []


@pytest.mark.parametrize("answer", ["余额说错了，是2000元", "已退款说错了，是2000元"])
def test_other_amount_does_not_supersede_payment(answer):
    assert _explicit_correction(
        _state(), answer=answer,
        replacement=UserFact(fact_id="f.new", text=answer, source_turn=0),
        condition_id="cond.payment_made",
    ) == []


def _state():
    return QueryState(
        session_id="s-regression", initial_query="交了5000元",
        user_facts=[UserFact(fact_id="f.old", text="交了5000元", source_turn=0)],
        condition_states=[QueryConditionState(
            condition_id="cond.payment_made", status=ConditionStatus.SATISFIED,
            supporting_fact_ids=["f.old"],
            evidence=[ConditionEvidence(fact_id="f.old", relation="SUPPORTS", confidence=0.8, reason="原始证据")],
        )],
    )


@pytest.mark.parametrize("active", [False, True])
def test_corrected_only_evidence_is_not_restored(active):
    meta = SessionRuntimeMeta(corrections=[CorrectionLink(
        target_fact_id="f.old", condition_id="cond.payment_made", reason="明确纠正",
    )])
    catalog = SimpleNamespace(conditions={"cond.payment_made": SimpleNamespace(
        predicate="付款", label="付款",
    )}, rules={})
    with p4_request_context(meta):
        set_scope(RuleScope(
            active_rule_ids=(), alternative_rule_ids=(), background_rule_ids=(),
            active_condition_ids=("cond.payment_made",) if active else (),
        ))
        result = SemanticConditionProjector(catalog).project(_state(), [])
    condition = result.condition_states[0]
    assert condition.status == ConditionStatus.UNKNOWN
    assert condition.evidence == []
    assert condition.supporting_fact_ids == []


def test_ambiguous_money_correction_does_not_pick_latest():
    state = _state()
    state.user_facts.append(UserFact(fact_id="f.other", text="退了1000元", source_turn=0))
    state.condition_states[0].evidence.append(ConditionEvidence(
        fact_id="f.other", relation="SUPPORTS", confidence=0.8, reason="另一证据",
    ))
    answer = "金额说错了，应该是6000元"
    result = _explicit_correction(
        state, answer=answer,
        replacement=UserFact(fact_id="f.new", text=answer, source_turn=0),
        condition_id="cond.payment_made",
    )
    assert result == []


def test_invalid_quote_fails_without_completing_ledger():
    class BadModel:
        def generate_structured(self, **kwargs):
            return ConditionLinkBatch(links=[ConditionLinkDraft(
                fact_id="f.old", condition_id="cond.payment_made",
                quoted_span="用户从未说过的句子", relation="SUPPORTS",
                rationale="错误引句", raw_confidence=0.8,
            )])

    catalog = SimpleNamespace(conditions={"cond.payment_made": SimpleNamespace(
        predicate="付款", label="付款",
    )}, rules={})
    meta = SessionRuntimeMeta()
    with p4_request_context(meta):
        set_scope(RuleScope(
            active_rule_ids=(), alternative_rule_ids=(), background_rule_ids=(),
            active_condition_ids=("cond.payment_made",),
        ))
        state = SemanticConditionProjector(catalog, BadModel()).project(_state(), [])
        # 坏引句被丢弃：不入账，允许下轮重试；不得写成假 NO_RELEVANT。
        assert meta.projection_ledger == []
        assert state.condition_states[0].status == ConditionStatus.SATISFIED


def test_evidenced_condition_remaps_when_active_scope_empty():
    """A 本轮无 ACTIVE 时，已有付款证据仍须能接收新事实的 OPPOSES；较新轮次优先。"""

    class Model:
        def generate_structured(self, **kwargs):
            return ConditionLinkBatch(
                links=[
                    ConditionLinkDraft(
                        fact_id="f.new",
                        condition_id="cond.payment_made",
                        quoted_span="其实我还没付款",
                        relation="OPPOSES",
                        rationale="否定付款",
                        raw_confidence=0.9,
                    )
                ]
            )

    catalog = SimpleNamespace(
        conditions={"cond.payment_made": SimpleNamespace(predicate="付款", label="付款")},
        rules={},
    )
    state = _state()
    state.user_facts.append(
        UserFact(fact_id="f.new", text="其实我还没付款。我办了卡，合同成立了。", source_turn=1)
    )
    meta = SessionRuntimeMeta()
    with p4_request_context(meta):
        set_scope(
            RuleScope(
                active_rule_ids=(),
                alternative_rule_ids=(),
                background_rule_ids=(),
                active_condition_ids=(),
            )
        )
        out = SemanticConditionProjector(catalog, Model()).project(state, [])
    payment = next(item for item in out.condition_states if item.condition_id == "cond.payment_made")
    assert payment.status == ConditionStatus.NOT_SATISFIED
    assert {item.relation for item in payment.evidence} == {"SUPPORTS", "OPPOSES"}
    assert any("按更新轮次裁决" in reason for reason in payment.mapping_reasons)


def test_correction_target_condition_remaps_replacement_after_evidence_cleared():
    """纠正清空付款旧证据后，替换事实仍须能映射到付款条件。"""

    class Model:
        def generate_structured(self, **kwargs):
            return ConditionLinkBatch(
                links=[
                    ConditionLinkDraft(
                        fact_id="f.new",
                        condition_id="cond.payment_made",
                        quoted_span="是6000元",
                        relation="SUPPORTS",
                        rationale="新金额",
                        raw_confidence=0.9,
                    )
                ]
            )

    catalog = SimpleNamespace(
        conditions={"cond.payment_made": SimpleNamespace(predicate="付款", label="付款")},
        rules={},
    )
    state = QueryState(
        session_id="s-corr-remap",
        initial_query="交了5000元",
        user_facts=[
            UserFact(fact_id="f.old", text="交了5000元", source_turn=0),
            UserFact(fact_id="f.new", text="金额说错了，是6000元。", source_turn=1),
        ],
        dialogue_history=[
            DialogueTurn(
                turn_id=1,
                question_id="q.1",
                condition_id="cond.payment_made",
                question="金额？",
                answer="金额说错了，是6000元。",
            )
        ],
        condition_states=[
            QueryConditionState(
                condition_id="cond.payment_made",
                status=ConditionStatus.UNKNOWN,
                supporting_fact_ids=[],
                evidence=[],
                mapping_reasons=["旧证据已纠正退出"],
            )
        ],
    )
    meta = SessionRuntimeMeta(
        corrections=[
            CorrectionLink(
                target_fact_id="f.old",
                replacement_fact_id="f.new",
                condition_id="cond.payment_made",
                reason="明确纠正",
            )
        ]
    )
    with p4_request_context(meta):
        set_scope(
            RuleScope(
                active_rule_ids=(),
                alternative_rule_ids=(),
                background_rule_ids=(),
                active_condition_ids=(),
            )
        )
        out = SemanticConditionProjector(catalog, Model()).project(state, [])
    payment = next(item for item in out.condition_states if item.condition_id == "cond.payment_made")
    assert payment.status == ConditionStatus.SATISFIED
    assert payment.supporting_fact_ids == ["f.new"]


def test_correction_qualifies_upgraded_to_supports():
    """LLM 对纠正替换句误判 QUALIFIES 时，按 corrections 目标条件升为 SUPPORTS。"""

    class Model:
        def generate_structured(self, **kwargs):
            payload = __import__("json").loads(kwargs["user_prompt"])
            assert "corrections" in payload
            assert payload["corrections"][0]["replacement_fact_id"] == "f.new"
            return ConditionLinkBatch(
                links=[
                    ConditionLinkDraft(
                        fact_id="f.new",
                        condition_id="cond.payment_made",
                        quoted_span="是6000元",
                        relation="QUALIFIES",
                        rationale="只改了金额，未重述付款事件",
                        raw_confidence=0.7,
                    )
                ]
            )

    catalog = SimpleNamespace(
        conditions={"cond.payment_made": SimpleNamespace(predicate="付款", label="付款")},
        rules={},
    )
    state = QueryState(
        session_id="s-corr-upgrade",
        initial_query="交了5000元",
        user_facts=[
            UserFact(fact_id="f.old", text="交了5000元", source_turn=0),
            UserFact(fact_id="f.new", text="金额说错了，是6000元。", source_turn=1),
        ],
        dialogue_history=[
            DialogueTurn(
                turn_id=1,
                question_id="q.1",
                condition_id="cond.payment_made",
                question="金额？",
                answer="金额说错了，是6000元。",
            )
        ],
        condition_states=[
            QueryConditionState(
                condition_id="cond.payment_made",
                status=ConditionStatus.UNKNOWN,
                supporting_fact_ids=[],
                evidence=[],
                mapping_reasons=["旧证据已纠正退出"],
            )
        ],
    )
    meta = SessionRuntimeMeta(
        corrections=[
            CorrectionLink(
                target_fact_id="f.old",
                replacement_fact_id="f.new",
                condition_id="cond.payment_made",
                reason="明确纠正",
            )
        ]
    )
    with p4_request_context(meta):
        set_scope(
            RuleScope(
                active_rule_ids=(),
                alternative_rule_ids=(),
                background_rule_ids=(),
                active_condition_ids=("cond.payment_made",),
            )
        )
        out = SemanticConditionProjector(catalog, Model()).project(state, [])
    payment = next(item for item in out.condition_states if item.condition_id == "cond.payment_made")
    assert payment.status == ConditionStatus.SATISFIED
    assert payment.evidence[0].relation == "SUPPORTS"
    assert payment.evidence[0].fact_id == "f.new"


def test_projection_schema_failure_soft_degrades_without_503():
    """投影 schema 连续失败时保留历史证据，不抛 LanguageModelUnavailable。"""
    from casepath.application.errors import InvalidComponentOutput

    class BadModel:
        calls = 0

        def generate_structured(self, **kwargs):
            BadModel.calls += 1
            raise InvalidComponentOutput("不符合目标合同")

    catalog = SimpleNamespace(
        conditions={"cond.payment_made": SimpleNamespace(predicate="付款", label="付款")},
        rules={},
    )
    meta = SessionRuntimeMeta()
    with p4_request_context(meta):
        set_scope(
            RuleScope(
                active_rule_ids=(),
                alternative_rule_ids=(),
                background_rule_ids=(),
                active_condition_ids=("cond.payment_made",),
            )
        )
        out = SemanticConditionProjector(catalog, BadModel()).project(_state(), [])
    assert BadModel.calls == 2
    payment = out.condition_states[0]
    assert payment.status == ConditionStatus.SATISFIED
    assert payment.evidence[0].fact_id == "f.old"
    # 失败配对不得写成假 NO_RELEVANT，否则下轮无法重试。
    assert meta.projection_ledger == []
