from casepath.adapters.search import BM25CaseRetriever
from casepath.contracts import (
    CandidateClaim,
    CaseRecord,
    ClaimRecord,
    ConditionFinding,
    ConditionStatus,
    DecisionItem,
    DecisionStatus,
    MaturityLevel,
    QueryConditionState,
    QueryState,
    ScoredReference,
    SourceSpan,
)


def make_case(
    case_id: str,
    *,
    title: str = "健身房停业退款纠纷",
    decision_status: DecisionStatus = DecisionStatus.GRANTED,
    invoked_rule_ids: list[str] | None = None,
    condition_status: ConditionStatus = ConditionStatus.SATISFIED,
    maturity: MaturityLevel = MaturityLevel.L2,
) -> CaseRecord:
    """创建具备检索、重排和分组字段的案例测试对象。"""

    return CaseRecord(
        case_id=case_id,
        title=title,
        cause="服务合同纠纷",
        maturity=maturity,
        claims=[
            ClaimRecord(
                claim_id=f"claim.{case_id}",
                claim_type="服务合同解除与返还",
                requested_remedy="解除合同并返还未消费余额",
                invoked_rule_ids=invoked_rule_ids or [],
            )
        ],
        condition_findings=[
            ConditionFinding(
                condition_id="cond.performance_impossible",
                status=condition_status,
                confidence=0.9,
                source_span_ids=[f"span.fact.{case_id}"],
                human_verified=True,
            )
        ],
        decisions=[
            DecisionItem(
                decision_id=f"decision.{case_id}",
                claim_id=f"claim.{case_id}",
                status=decision_status,
                description="裁判结果仅用于召回后的案例角色分类",
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
                quote="经营者停止营业且没有替代门店",
            )
        ],
    )


def make_state() -> QueryState:
    """创建包含一个已知关键条件的用户查询。"""

    return QueryState(
        session_id="session.1",
        initial_query="健身房停止营业，要求返还未消费余额",
        candidate_claims=[
            CandidateClaim(
                claim_type="服务合同解除与返还",
                requested_remedy="返还未消费余额",
                confidence=0.9,
            )
        ],
        condition_states=[
            QueryConditionState(
                condition_id="cond.performance_impossible",
                status=ConditionStatus.SATISFIED,
                supporting_fact_ids=["fact.closed"],
            )
        ],
    )


def make_rule_refs() -> list[ScoredReference]:
    """创建案例重排使用的候选规则引用。"""

    return [
        ScoredReference(
            object_id="rule.service.refund",
            score=1.0,
            reasons=["测试候选规则"],
        )
    ]


def test_case_retriever_builds_support_and_limiting_sets():
    """支持和驳回相同请求的真实案例应进入不同对照集合。"""

    support = make_case(
        "case.support",
        decision_status=DecisionStatus.GRANTED,
        invoked_rule_ids=["rule.service.refund"],
    )
    limiting = make_case(
        "case.limiting",
        decision_status=DecisionStatus.REJECTED,
        invoked_rule_ids=["rule.service.refund"],
        condition_status=ConditionStatus.NOT_SATISFIED,
    )
    retriever = BM25CaseRetriever([support, limiting])

    bundle = retriever.retrieve(make_state(), make_rule_refs())

    assert [item.object_id for item in bundle.support_case_refs] == ["case.support"]
    assert [item.object_id for item in bundle.limiting_case_refs] == ["case.limiting"]
    assert bundle.degraded is False


def test_unrelated_granted_claim_does_not_turn_case_into_support():
    """同案无关请求获支持时，当前被驳回的请求仍应归入限制案例。"""

    case = make_case(
        "case.multiple-claims",
        decision_status=DecisionStatus.REJECTED,
    )
    unrelated_claim = ClaimRecord(
        claim_id="claim.unrelated",
        claim_type="精神损害赔偿",
        requested_remedy="赔偿精神损失",
    )
    unrelated_decision = DecisionItem(
        decision_id="decision.unrelated",
        claim_id="claim.unrelated",
        status=DecisionStatus.GRANTED,
        description="支持与预付费退款无关的另一项请求",
        source_span_ids=["span.decision.unrelated"],
    )
    case = case.model_copy(
        update={
            "claims": [*case.claims, unrelated_claim],
            "decisions": [*case.decisions, unrelated_decision],
        }
    )
    retriever = BM25CaseRetriever([case])

    bundle = retriever.retrieve(make_state(), make_rule_refs())

    assert bundle.support_case_refs == []
    assert [item.object_id for item in bundle.limiting_case_refs] == [
        "case.multiple-claims"
    ]


def test_case_retriever_uses_rule_overlap_in_reranking():
    """BM25相关性相同时，与候选规则重合的案例应获得更高最终分数。"""

    matching = make_case(
        "case.matching-rule",
        invoked_rule_ids=["rule.service.refund"],
    )
    unrelated = make_case(
        "case.other-rule",
        invoked_rule_ids=["rule.other.claims"],
    )
    retriever = BM25CaseRetriever([unrelated, matching])

    bundle = retriever.retrieve(make_state(), make_rule_refs())

    assert [item.object_id for item in bundle.support_case_refs] == [
        "case.matching-rule",
        "case.other-rule",
    ]
    assert bundle.support_case_refs[0].score > bundle.support_case_refs[1].score


def test_case_retriever_reports_score_components():
    """每个案例结果都应解释四项重排特征，便于检查排序原因。"""

    case = make_case(
        "case.support",
        invoked_rule_ids=["rule.service.refund"],
    )
    retriever = BM25CaseRetriever([case])

    reference = retriever.retrieve(make_state(), make_rule_refs()).support_case_refs[0]

    assert any("BM25归一化分数" in reason for reason in reference.reasons)
    assert any("条件重合分数" in reason for reason in reference.reasons)
    assert any("规则重合分数" in reason for reason in reference.reasons)
    assert any("来源质量分数" in reason for reason in reference.reasons)


def test_case_retriever_degrades_without_real_contrast_case():
    """没有限制或边界案例时必须显式降级，不能伪造对照材料。"""

    retriever = BM25CaseRetriever([make_case("case.support")])

    bundle = retriever.retrieve(make_state(), make_rule_refs())

    assert bundle.limiting_case_refs == []
    assert bundle.boundary_case_refs == []
    assert bundle.degraded is True
    assert bundle.degradation_reason is not None


def test_placeholder_case_is_excluded_from_formal_results():
    """ID中标记为placeholder的演示案例不得进入正式检索结果。"""

    placeholder = make_case(
        "case.demo-limiting.placeholder",
        decision_status=DecisionStatus.REJECTED,
    )
    retriever = BM25CaseRetriever([placeholder])

    bundle = retriever.retrieve(make_state(), make_rule_refs())

    assert bundle.support_case_refs == []
    assert bundle.limiting_case_refs == []
    assert bundle.boundary_case_refs == []
    assert bundle.degraded is True


def test_case_retriever_respects_bm25_top_k():
    """只有BM25初始召回Top-K中的案例可以进入后续重排和分组。"""

    cases = [make_case(f"case.{index}") for index in range(5)]
    retriever = BM25CaseRetriever(cases, top_k=2)

    bundle = retriever.retrieve(make_state(), make_rule_refs())

    total = (
        len(bundle.support_case_refs)
        + len(bundle.limiting_case_refs)
        + len(bundle.boundary_case_refs)
    )
    assert total == 2


def test_case_retrieval_is_deterministic():
    """相同输入必须产生完全一致的案例集合、顺序、分数和解释。"""

    retriever = BM25CaseRetriever(
        [
            make_case("case.b"),
            make_case("case.a"),
        ]
    )
    state = make_state()
    rule_refs = make_rule_refs()

    first = retriever.retrieve(state, rule_refs)
    second = retriever.retrieve(state, rule_refs)

    assert first == second
    assert [item.object_id for item in first.support_case_refs] == ["case.a", "case.b"]


def test_empty_case_repository_returns_degraded_bundle():
    """空案例库应返回带降级原因的合法RetrievalBundle。"""

    bundle = BM25CaseRetriever([]).retrieve(make_state(), make_rule_refs())

    assert bundle.rule_refs == make_rule_refs()
    assert bundle.support_case_refs == []
    assert bundle.limiting_case_refs == []
    assert bundle.degraded is True


def test_retrieved_case_source_spans_are_exposed_for_later_citation():
    """进入结果集合的案例原文跨度ID应传给后续解释与引用校验模块。"""

    retriever = BM25CaseRetriever([make_case("case.support")])

    bundle = retriever.retrieve(make_state(), make_rule_refs())

    assert bundle.cited_span_ids == ["span.fact.case.support"]
