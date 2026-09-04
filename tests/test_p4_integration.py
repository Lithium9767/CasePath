from casepath.adapters import (
    BM25CaseRetriever,
    BM25RuleRetriever,
    RuleConditionProjector,
    WeightedQuestionPolicy,
)
from casepath.bootstrap import build_p4_workflow
from casepath.contracts import (
    CaseRecord,
    ClaimRecord,
    ConditionFinding,
    ConditionStatus,
    DecisionItem,
    DecisionStatus,
    LegalConsequence,
    MaturityLevel,
    ProvisionRef,
    QueryState,
    RuleCondition,
    RuleRecord,
    SourceSpan,
)


def make_rule() -> RuleRecord:
    """创建端到端测试使用的服务合同解除规则。"""

    return RuleRecord(
        rule_id="rule.service.refund",
        title="健身服务合同解除与预付费退款",
        claim_types=["服务合同解除与返还"],
        provisions=[
            ProvisionRef(
                source_id="source.civil-code",
                provision_id="provision.563",
                article_no="563",
                title="合同法定解除",
            )
        ],
        conditions=[
            RuleCondition(
                condition_id="cond.performance_impossible",
                label="经营者不能继续履行",
                predicate="经营者永久停止经营",
                required=True,
                user_answerable=True,
                evidence_types=["停业通知", "企业登记状态"],
            ),
            RuleCondition(
                condition_id="cond.alternative_performance",
                label="存在替代履行方式",
                predicate="经营者可以通过其他门店继续提供服务",
                required=False,
                user_answerable=True,
                evidence_types=["聊天记录"],
            ),
        ],
        consequences=[
            LegalConsequence(
                consequence_id="consequence.refund",
                consequence_type="解除与返还",
                description="满足条件时可以解除合同并请求返还未消费余额",
            )
        ],
        maturity=MaturityLevel.L3,
        source_spans=[
            SourceSpan(
                span_id="span.civil-code.563",
                source_id="source.civil-code",
                section="第五百六十三条",
                start_offset=0,
                end_offset=8,
                quote="当事人可以解除合同",
            )
        ],
    )


def make_case(
    case_id: str,
    *,
    decision_status: DecisionStatus,
    performance_status: ConditionStatus,
) -> CaseRecord:
    """创建带有关键条件认定和真实裁判角色的案例。"""

    return CaseRecord(
        case_id=case_id,
        title="健身房停业预付费退款纠纷",
        cause="服务合同纠纷",
        maturity=MaturityLevel.L3,
        claims=[
            ClaimRecord(
                claim_id=f"claim.{case_id}",
                claim_type="服务合同解除与返还",
                requested_remedy="解除合同并返还未消费余额",
                invoked_rule_ids=["rule.service.refund"],
            )
        ],
        condition_findings=[
            ConditionFinding(
                condition_id="cond.performance_impossible",
                status=performance_status,
                confidence=0.95,
                source_span_ids=[f"span.fact.{case_id}"],
                human_verified=True,
            )
        ],
        decisions=[
            DecisionItem(
                decision_id=f"decision.{case_id}",
                claim_id=f"claim.{case_id}",
                status=decision_status,
                description="用于召回后案例角色分类的裁判结果",
                source_span_ids=[f"span.decision.{case_id}"],
            )
        ],
        source_spans=[
            SourceSpan(
                span_id=f"span.fact.{case_id}",
                source_id=f"source.{case_id}",
                section="本院查明",
                start_offset=0,
                end_offset=10,
                quote="经营者是否已经永久停止经营",
            )
        ],
    )


def make_cases() -> list[CaseRecord]:
    """创建一个真实支持案例和一个真实限制案例。"""

    return [
        make_case(
            "case.support",
            decision_status=DecisionStatus.GRANTED,
            performance_status=ConditionStatus.SATISFIED,
        ),
        make_case(
            "case.limiting",
            decision_status=DecisionStatus.REJECTED,
            performance_status=ConditionStatus.NOT_SATISFIED,
        ),
    ]


def make_initial_state() -> QueryState:
    """创建信息不足、需要进行两轮条件澄清的初始状态。"""

    return QueryState(
        session_id="session.integration",
        initial_query="健身房关门了，我想退还没有消费的预付费余额",
    )


def test_build_p4_workflow_wires_real_algorithm_components():
    """P4构建函数必须装配真实实现，而不是继续使用Demo适配器。"""

    workflow = build_p4_workflow(rules=[make_rule()], cases=make_cases())
    dependencies = workflow.dependencies

    assert isinstance(dependencies.rule_retriever, BM25RuleRetriever)
    assert isinstance(dependencies.case_retriever, BM25CaseRetriever)
    assert isinstance(dependencies.condition_projector, RuleConditionProjector)
    assert isinstance(dependencies.question_policy, WeightedQuestionPolicy)


def test_initial_run_retrieves_data_and_asks_high_value_question():
    """初始查询应完成真实检索、条件投影并提出最高价值问题。"""

    workflow = build_p4_workflow(rules=[make_rule()], cases=make_cases())

    snapshot = workflow.run(make_initial_state())

    assert snapshot.retrieval_bundle.rule_refs[0].object_id == "rule.service.refund"
    assert [
        reference.object_id
        for reference in snapshot.retrieval_bundle.support_case_refs
    ] == ["case.support"]
    assert [
        reference.object_id
        for reference in snapshot.retrieval_bundle.limiting_case_refs
    ] == ["case.limiting"]
    assert snapshot.retrieval_bundle.degraded is False
    assert snapshot.next_question is not None
    assert snapshot.next_question.condition_id == "cond.performance_impossible"
    assert snapshot.next_question.utility == 1.0


def test_answers_advance_to_next_condition_without_repeating_question():
    """回答关键条件后，应转向下一条件而不是重复原问题。"""

    workflow = build_p4_workflow(rules=[make_rule()], cases=make_cases())
    initial = workflow.run(make_initial_state())

    after_first_answer = workflow.apply_answer(
        initial.query_state,
        condition_id="cond.performance_impossible",
        answer="工商登记已经完成注销",
        status=ConditionStatus.SATISFIED,
    )

    assert after_first_answer.next_question is not None
    assert after_first_answer.next_question.condition_id == "cond.alternative_performance"
    assert all(
        turn.condition_id == "cond.performance_impossible"
        for turn in after_first_answer.query_state.dialogue_history
    )


def test_second_answer_stops_clarification_and_preserves_fact_sources():
    """两个活动条件均被回答后，应停止追问并保留事实来源。"""

    workflow = build_p4_workflow(rules=[make_rule()], cases=make_cases())
    initial = workflow.run(make_initial_state())
    after_first = workflow.apply_answer(
        initial.query_state,
        condition_id="cond.performance_impossible",
        answer="工商登记已经完成注销",
        status=ConditionStatus.SATISFIED,
    )
    after_second = workflow.apply_answer(
        after_first.query_state,
        condition_id="cond.alternative_performance",
        answer="没有其他门店，也不能转店",
        status=ConditionStatus.NOT_SATISFIED,
    )

    assert after_second.next_question is None
    assert after_second.query_state.status.value == "READY_TO_EXPLAIN"
    assert "STOP_CLARIFICATION" in after_second.trace
    assert len(after_second.query_state.dialogue_history) == 2
    by_id = {
        item.condition_id: item for item in after_second.query_state.condition_states
    }
    assert by_id["cond.performance_impossible"].status == ConditionStatus.SATISFIED
    assert by_id["cond.alternative_performance"].status == ConditionStatus.NOT_SATISFIED
    assert by_id["cond.performance_impossible"].supporting_fact_ids
    assert by_id["cond.alternative_performance"].supporting_fact_ids


def test_empty_case_repository_keeps_workflow_running_in_degraded_mode():
    """没有案例数据时仍应完成规则投影和追问，并明确标记降级。"""

    workflow = build_p4_workflow(rules=[make_rule()], cases=[])

    snapshot = workflow.run(make_initial_state())

    assert snapshot.retrieval_bundle.degraded is True
    assert snapshot.retrieval_bundle.degradation_reason is not None
    assert snapshot.next_question is not None
    assert snapshot.next_question.condition_id == "cond.performance_impossible"


def test_fixed_input_produces_deterministic_p4_result():
    """同一工作流对同一QueryState运行两次应产生完全一致的快照。"""

    workflow = build_p4_workflow(rules=[make_rule()], cases=make_cases())
    state = make_initial_state()

    assert workflow.run(state) == workflow.run(state)
