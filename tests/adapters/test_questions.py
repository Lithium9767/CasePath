from casepath.adapters.questions import QuestionTemplate, WeightedQuestionPolicy
from casepath.contracts import (
    CaseRecord,
    ClaimRecord,
    ConditionFinding,
    ConditionStatus,
    DialogueTurn,
    MaturityLevel,
    ProvisionRef,
    QueryConditionState,
    QueryState,
    RetrievalBundle,
    RuleCondition,
    RuleRecord,
    ScoredReference,
)


REQUIRED_CONDITION_ID = "cond.performance_impossible"
OPTIONAL_CONDITION_ID = "cond.alternative_performance"


def make_rule(
    *,
    optional_first: bool = False,
    optional_answerable: bool = True,
) -> RuleRecord:
    """创建同时包含必要条件和非必要条件的测试规则。"""

    required = RuleCondition(
        condition_id=REQUIRED_CONDITION_ID,
        label="经营者不能继续履行",
        predicate="经营者永久停业",
        required=True,
        user_answerable=True,
        evidence_types=["停业通知", "企业登记状态"],
    )
    optional = RuleCondition(
        condition_id=OPTIONAL_CONDITION_ID,
        label="存在替代履行方式",
        predicate="可以通过其他门店继续提供服务",
        required=False,
        user_answerable=optional_answerable,
        evidence_types=[],
    )
    return RuleRecord(
        rule_id="rule.service.refund",
        title="服务合同解除与返还",
        claim_types=["服务合同解除与返还"],
        provisions=[
            ProvisionRef(
                source_id="source.civil-code",
                provision_id="provision.563",
                article_no="563",
                title="合同法定解除",
            )
        ],
        conditions=[optional, required] if optional_first else [required, optional],
        maturity=MaturityLevel.L3,
    )


def make_case(
    case_id: str,
    *,
    condition_status: ConditionStatus,
) -> CaseRecord:
    """创建带有一个关键条件认定的案例。"""

    return CaseRecord(
        case_id=case_id,
        title="健身房预付费纠纷",
        cause="服务合同纠纷",
        maturity=MaturityLevel.L2,
        claims=[
            ClaimRecord(
                claim_id=f"claim.{case_id}",
                claim_type="服务合同解除与返还",
                requested_remedy="返还未消费余额",
            )
        ],
        condition_findings=[
            ConditionFinding(
                condition_id=REQUIRED_CONDITION_ID,
                status=condition_status,
                confidence=0.9,
                source_span_ids=[f"span.{case_id}"],
                human_verified=True,
            )
        ],
    )


def make_bundle() -> RetrievalBundle:
    """创建包含真实支持和限制案例的检索包。"""

    return RetrievalBundle(
        rule_refs=[
            ScoredReference(
                object_id="rule.service.refund",
                score=1.0,
                reasons=["测试规则"],
            )
        ],
        support_case_refs=[
            ScoredReference(
                object_id="case.support",
                score=0.9,
                reasons=["支持案例"],
            )
        ],
        limiting_case_refs=[
            ScoredReference(
                object_id="case.limiting",
                score=0.8,
                reasons=["限制案例"],
            )
        ],
    )


def make_state(
    *,
    required_status: ConditionStatus = ConditionStatus.UNKNOWN,
    optional_status: ConditionStatus = ConditionStatus.UNKNOWN,
    dialogue_history: list[DialogueTurn] | None = None,
) -> QueryState:
    """创建包含两个待判断条件的查询状态。"""

    return QueryState(
        session_id="session.1",
        initial_query="健身房关门了，我想退还余额",
        condition_states=[
            QueryConditionState(
                condition_id=REQUIRED_CONDITION_ID,
                status=required_status,
            ),
            QueryConditionState(
                condition_id=OPTIONAL_CONDITION_ID,
                status=optional_status,
            ),
        ],
        dialogue_history=dialogue_history or [],
    )


def make_policy(
    *,
    rule: RuleRecord | None = None,
    minimum_utility: float = 0.25,
    max_questions: int = 5,
) -> WeightedQuestionPolicy:
    """构建包含正反案例的追问策略。"""

    selected_rule = rule or make_rule()
    support = make_case(
        "case.support",
        condition_status=ConditionStatus.SATISFIED,
    )
    limiting = make_case(
        "case.limiting",
        condition_status=ConditionStatus.NOT_SATISFIED,
    )
    return WeightedQuestionPolicy(
        rules_by_id={selected_rule.rule_id: selected_rule},
        cases_by_id={
            support.case_id: support,
            limiting.case_id: limiting,
        },
        minimum_utility=minimum_utility,
        max_questions=max_questions,
    )


def test_policy_selects_highest_utility_condition():
    """必要且存在正反案例分歧的条件应优先于非必要条件。"""

    question = make_policy().select(make_state(), make_bundle())

    assert question is not None
    assert question.condition_id == REQUIRED_CONDITION_ID
    assert question.question == "健身房是永久停止经营，还是暂时关闭？"
    assert question.options == [
        "永久停止经营",
        "暂时关闭",
        "仍可在其他门店使用",
        "不清楚",
    ]


def test_question_contains_complete_score_components():
    """问题必须暴露四项正向特征和一项交互成本。"""

    question = make_policy().select(make_state(), make_bundle())

    assert question is not None
    components = {component.name: component.value for component in question.score_components}
    assert components == {
        "rule_centrality": 1.0,
        "case_contrast": 1.0,
        "answerability": 1.0,
        "evidence_availability": 1.0,
        "interaction_cost": 0.0,
    }
    assert question.utility == 1.0


def test_question_exposes_relevant_support_and_limiting_cases():
    """问题应携带在当前条件上有认定的正反案例ID。"""

    question = make_policy().select(make_state(), make_bundle())

    assert question is not None
    assert question.supporting_case_ids == ["case.support"]
    assert question.limiting_case_ids == ["case.limiting"]
    assert "案例" in question.why_asked


def test_answered_condition_is_not_asked_again():
    """用户已经回答过的条件不能被重复询问。"""

    history = [
        DialogueTurn(
            turn_id=1,
            condition_id=REQUIRED_CONDITION_ID,
            question="健身房是否永久停业？",
            answer="我不清楚",
        )
    ]

    question = make_policy().select(
        make_state(dialogue_history=history),
        make_bundle(),
    )

    assert question is not None
    assert question.condition_id == OPTIONAL_CONDITION_ID


def test_resolved_conditions_are_not_candidates():
    """SATISFIED和NOT_SATISFIED条件不能进入追问候选集合。"""

    question = make_policy().select(
        make_state(
            required_status=ConditionStatus.SATISFIED,
            optional_status=ConditionStatus.NOT_SATISFIED,
        ),
        make_bundle(),
    )

    assert question is None


def test_conflicting_unanswered_condition_can_be_clarified():
    """尚未针对性回答的CONFLICTING条件可以进入澄清候选。"""

    question = make_policy().select(
        make_state(required_status=ConditionStatus.CONFLICTING),
        make_bundle(),
    )

    assert question is not None
    assert question.condition_id == REQUIRED_CONDITION_ID
    assert "冲突" in question.why_asked


def test_policy_stops_after_maximum_questions():
    """达到最大交互轮数后必须停止，避免无休止追问。"""

    history = [
        DialogueTurn(
            turn_id=1,
            condition_id="cond.previous",
            question="此前的问题？",
            answer="此前的回答",
        )
    ]
    policy = make_policy(max_questions=1)

    assert policy.select(make_state(dialogue_history=history), make_bundle()) is None


def test_policy_stops_when_utility_is_below_threshold():
    """所有候选问题低于阈值时应返回None。"""

    policy = make_policy(minimum_utility=1.01)

    assert policy.select(make_state(), make_bundle()) is None


def test_non_answerable_condition_is_skipped():
    """规则明确标记为用户不可回答的条件不能生成用户问题。"""

    rule = make_rule(optional_answerable=False)
    state = make_state(required_status=ConditionStatus.SATISFIED)

    assert make_policy(rule=rule).select(state, make_bundle()) is None


def test_interaction_cost_is_subtracted_from_utility():
    """已有交互轮次应增加成本并降低下一问题的效用。"""

    history = [
        DialogueTurn(
            turn_id=1,
            condition_id="cond.previous",
            question="此前的问题？",
            answer="此前的回答",
        )
    ]
    question = make_policy().select(
        make_state(dialogue_history=history),
        make_bundle(),
    )

    assert question is not None
    components = {component.name: component.value for component in question.score_components}
    assert components["interaction_cost"] == -0.05
    assert question.utility == 0.95


def test_missing_contrast_cases_produce_zero_contrast_score():
    """没有真实对照案例时D项应为0，但必要条件仍可被追问。"""

    bundle = make_bundle().model_copy(
        update={"limiting_case_refs": [], "boundary_case_refs": []}
    )

    question = make_policy().select(make_state(), bundle)

    assert question is not None
    components = {component.name: component.value for component in question.score_components}
    assert components["case_contrast"] == 0.0


def test_equal_scores_are_ordered_by_condition_id():
    """候选条件完全同分时应按condition_id稳定排序。"""

    first = RuleCondition(
        condition_id="cond.b",
        label="条件B",
        predicate="测试条件B",
        required=True,
        user_answerable=True,
    )
    second = RuleCondition(
        condition_id="cond.a",
        label="条件A",
        predicate="测试条件A",
        required=True,
        user_answerable=True,
    )
    rule = make_rule().model_copy(update={"conditions": [first, second]})
    policy = WeightedQuestionPolicy(
        rules_by_id={rule.rule_id: rule},
        cases_by_id={},
    )
    state = QueryState(
        session_id="session.1",
        initial_query="测试",
        condition_states=[
            QueryConditionState(condition_id="cond.b"),
            QueryConditionState(condition_id="cond.a"),
        ],
    )

    question = policy.select(state, make_bundle())

    assert question is not None
    assert question.condition_id == "cond.a"


def test_custom_question_template_is_used():
    """调用方可以为新规则条件提供经过业务审核的问题模板。"""

    rule = make_rule()
    policy = WeightedQuestionPolicy(
        rules_by_id={rule.rule_id: rule},
        cases_by_id={},
        templates={
            REQUIRED_CONDITION_ID: QuestionTemplate(
                question="经营者是否已经注销？",
                options=("已经注销", "尚未注销", "不清楚"),
            )
        },
    )

    question = policy.select(make_state(), make_bundle())

    assert question is not None
    assert question.question == "经营者是否已经注销？"
    assert question.options == ["已经注销", "尚未注销", "不清楚"]
