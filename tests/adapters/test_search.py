from casepath.adapters.search import (
    BM25RuleRetriever,
    build_case_search_text,
    build_query_text,
    build_rule_search_text,
)
from casepath.contracts import (
    CaseRecord,
    ClaimRecord,
    ConditionStatus,
    DecisionItem,
    DecisionStatus,
    DialogueTurn,
    LegalConsequence,
    MaturityLevel,
    ProvisionRef,
    QueryState,
    RuleCondition,
    RuleRecord,
    SourceSpan,
)


def make_rule(
    rule_id: str,
    *,
    title: str,
    claim_type: str,
    condition_label: str,
    consequence: str,
) -> RuleRecord:
    """创建只包含检索测试所需字段的规则。"""

    return RuleRecord(
        rule_id=rule_id,
        title=title,
        claim_types=[claim_type],
        provisions=[
            ProvisionRef(
                source_id="source.civil-code",
                provision_id=f"provision.{rule_id}",
                article_no="563",
                title="合同法定解除",
            )
        ],
        conditions=[
            RuleCondition(
                condition_id=f"condition.{rule_id}",
                label=condition_label,
                predicate="经营者不能继续履行合同义务",
            )
        ],
        consequences=[
            LegalConsequence(
                consequence_id=f"consequence.{rule_id}",
                consequence_type="合同解除",
                description=consequence,
            )
        ],
        maturity=MaturityLevel.L3,
        source_spans=[
            SourceSpan(
                span_id=f"span.{rule_id}",
                source_id="source.civil-code",
                section="第五百六十三条",
                start_offset=0,
                end_offset=8,
                quote="当事人可以解除合同",
            )
        ],
    )


def make_case(*, decision_status: DecisionStatus, decision_description: str) -> CaseRecord:
    """创建除裁判结果外完全相同的案例，用于验证答案泄漏防护。"""

    return CaseRecord(
        case_id="case.fitness.refund",
        title="健身房停业预付费纠纷",
        court="示例法院",
        cause="服务合同纠纷",
        maturity=MaturityLevel.L2,
        claims=[
            ClaimRecord(
                claim_id="claim.refund",
                claim_type="服务合同解除与返还",
                requested_remedy="解除合同并返还未消费余额",
            )
        ],
        decisions=[
            DecisionItem(
                decision_id="decision.refund",
                claim_id="claim.refund",
                status=decision_status,
                description=decision_description,
                amount=3000,
                source_span_ids=["span.decision"],
            )
        ],
        source_spans=[
            SourceSpan(
                span_id="span.fact",
                source_id="source.judgment",
                section="本院查明",
                start_offset=0,
                end_offset=10,
                quote="健身房停止经营且尚有余额",
            )
        ],
    )


def test_rule_search_text_contains_allowed_fields():
    """规则检索文本应包含标题、请求权、条件、后果和可追溯原文。"""

    rule = make_rule(
        "rule.service.refund",
        title="服务合同解除规则",
        claim_type="预付费退款",
        condition_label="经营者无法继续履行",
        consequence="合同解除后返还未消费余额",
    )

    text = build_rule_search_text(rule)

    assert "服务合同解除规则" in text
    assert "预付费退款" in text
    assert "经营者无法继续履行" in text
    assert "合同解除后返还未消费余额" in text
    assert "当事人可以解除合同" in text


def test_case_search_text_excludes_decision_fields():
    """改变裁判结果不得改变案例召回文本，防止答案字段泄漏。"""

    granted = make_case(
        decision_status=DecisionStatus.GRANTED,
        decision_description="支持返还全部余额",
    )
    rejected = make_case(
        decision_status=DecisionStatus.REJECTED,
        decision_description="驳回全部诉讼请求",
    )

    granted_text = build_case_search_text(granted)
    rejected_text = build_case_search_text(rejected)

    assert granted_text == rejected_text
    assert "支持返还全部余额" not in granted_text
    assert "驳回全部诉讼请求" not in rejected_text
    assert "GRANTED" not in granted_text
    assert "REJECTED" not in rejected_text


def test_query_text_includes_initial_query_and_nonempty_answers():
    """后续检索应同时利用初始问题和用户已经给出的有效回答。"""

    state = QueryState(
        session_id="session.1",
        initial_query="健身房关门后能否退款？",
        dialogue_history=[
            DialogueTurn(
                turn_id=1,
                condition_id="cond.performance_impossible",
                question="是否永久停业？",
                answer="所有门店都已永久关闭",
            ),
            DialogueTurn(
                turn_id=2,
                condition_id="cond.alternative_performance",
                question="是否可以转到其他门店？",
                answer=None,
            ),
        ],
    )

    assert build_query_text(state) == "健身房关门后能否退款？ 所有门店都已永久关闭"


def test_rule_retriever_ranks_relevant_rule_first():
    """与查询包含更多共同词元的退款规则应排在第一位。"""

    refund_rule = make_rule(
        "rule.service.refund",
        title="健身服务合同解除与退款",
        claim_type="健身房预付费退款",
        condition_label="健身房永久停业",
        consequence="返还未消费余额",
    )
    lease_rule = make_rule(
        "rule.house.lease",
        title="房屋租赁合同解除",
        claim_type="房屋租赁纠纷",
        condition_label="出租人无法交付房屋",
        consequence="承租人可以解除租赁合同",
    )
    retriever = BM25RuleRetriever([lease_rule, refund_rule])

    results = retriever.retrieve(
        QueryState(
            session_id="session.1",
            initial_query="健身房永久停业，要求退还预付费余额",
        )
    )

    assert results
    assert results[0].object_id == refund_rule.rule_id
    assert results[0].score > 0
    assert any("BM25规则召回" in reason for reason in results[0].reasons)
    assert any("命中词元" in reason for reason in results[0].reasons)


def test_rule_retriever_respects_top_k():
    """规则检索器返回数量不能超过构造时指定的Top-K。"""

    rules = [
        make_rule(
            f"rule.{index}",
            title=f"合同解除规则{index}",
            claim_type="合同解除",
            condition_label="不能继续履行",
            consequence="可以解除合同",
        )
        for index in range(3)
    ]
    retriever = BM25RuleRetriever(rules, top_k=2)

    results = retriever.retrieve(
        QueryState(session_id="session.1", initial_query="合同解除")
    )

    assert len(results) == 2


def test_empty_rule_repository_returns_empty_result():
    """没有规则数据时，检索器应返回空结果而不是抛出异常。"""

    retriever = BM25RuleRetriever([])

    results = retriever.retrieve(
        QueryState(session_id="session.1", initial_query="合同解除")
    )

    assert results == []


def test_rule_retrieval_is_deterministic():
    """相同规则库和查询应产生完全相同的排序、分数与解释。"""

    rules = [
        make_rule(
            "rule.b",
            title="合同解除",
            claim_type="解除合同",
            condition_label="无法履行",
            consequence="终止合同",
        ),
        make_rule(
            "rule.a",
            title="合同解除",
            claim_type="解除合同",
            condition_label="无法履行",
            consequence="终止合同",
        ),
    ]
    retriever = BM25RuleRetriever(rules)
    state = QueryState(session_id="session.1", initial_query="解除合同")

    assert retriever.retrieve(state) == retriever.retrieve(state)
    assert [item.object_id for item in retriever.retrieve(state)] == ["rule.a", "rule.b"]


def test_existing_condition_state_does_not_need_to_be_embedded_in_query_text():
    """查询文本只使用用户原话，不把内部枚举值拼入自然语言检索。"""

    state = QueryState(
        session_id="session.1",
        initial_query="健身房停业",
        condition_states=[],
    )

    assert ConditionStatus.UNKNOWN.value not in build_query_text(state)
