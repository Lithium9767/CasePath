from casepath.adapters.projection import ConditionProjectionPattern, RuleConditionProjector
from casepath.contracts import (
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
    UserFact,
)


PERFORMANCE_CONDITION_ID = "cond.performance_impossible"
ALTERNATIVE_CONDITION_ID = "cond.alternative_performance"


def make_rule() -> RuleRecord:
    """创建包含两个可追问条件的最小规则。"""

    return RuleRecord(
        rule_id="rule.service.refund",
        title="服务合同解除与余额返还",
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
                condition_id=PERFORMANCE_CONDITION_ID,
                label="经营者不能继续履行",
                predicate="经营者永久停止经营且不能继续履行服务",
            ),
            RuleCondition(
                condition_id=ALTERNATIVE_CONDITION_ID,
                label="不存在替代履行方式",
                predicate="经营者不能通过其他门店继续提供服务",
            ),
        ],
        maturity=MaturityLevel.L3,
    )


def make_bundle() -> RetrievalBundle:
    """创建指向测试规则的检索包。"""

    return RetrievalBundle(
        rule_refs=[
            ScoredReference(
                object_id="rule.service.refund",
                score=1.0,
                reasons=["测试规则"],
            )
        ]
    )


def make_projector() -> RuleConditionProjector:
    """使用明确的正反短语构建测试投影器。"""

    rule = make_rule()
    return RuleConditionProjector(
        rules_by_id={rule.rule_id: rule},
        patterns={
            PERFORMANCE_CONDITION_ID: ConditionProjectionPattern(
                positive_phrases=("永久停业", "全部门店关闭", "停止经营"),
                negative_phrases=(
                    "暂时关闭",
                    "仍在营业",
                    "不是永久停业",
                    "并非永久停业",
                ),
            ),
            ALTERNATIVE_CONDITION_ID: ConditionProjectionPattern(
                positive_phrases=("没有其他门店", "不能转店"),
                negative_phrases=("可以转到其他门店", "仍可在其他门店使用"),
            ),
        },
    )


def get_condition_status(state: QueryState, condition_id: str) -> ConditionStatus:
    """从投影结果中读取指定条件的状态。"""

    by_id = {item.condition_id: item.status for item in state.condition_states}
    return by_id[condition_id]


def test_unmentioned_condition_remains_unknown():
    """用户没有提供的信息必须保持UNKNOWN，不能按缺词推断为否定。"""

    projected = make_projector().project(
        QueryState(session_id="session.1", initial_query="我想退还健身卡余额"),
        make_bundle(),
    )

    assert get_condition_status(projected, PERFORMANCE_CONDITION_ID) == ConditionStatus.UNKNOWN
    assert get_condition_status(projected, ALTERNATIVE_CONDITION_ID) == ConditionStatus.UNKNOWN


def test_positive_phrase_marks_condition_as_satisfied():
    """明确肯定短语应生成事实并将条件标记为SATISFIED。"""

    projected = make_projector().project(
        QueryState(
            session_id="session.1",
            initial_query="健身房已经永久停业，我想退还余额",
        ),
        make_bundle(),
    )

    condition = next(
        item
        for item in projected.condition_states
        if item.condition_id == PERFORMANCE_CONDITION_ID
    )
    assert condition.status == ConditionStatus.SATISFIED
    assert condition.supporting_fact_ids
    assert condition.supporting_fact_ids[0].startswith("fact.initial.")
    assert projected.user_facts[0].value is True


def test_negative_phrase_marks_condition_as_not_satisfied():
    """明确否定短语应映射为NOT_SATISFIED，而不是UNKNOWN。"""

    projected = make_projector().project(
        QueryState(
            session_id="session.1",
            initial_query="健身房只是暂时关闭，之后还会营业",
        ),
        make_bundle(),
    )

    assert (
        get_condition_status(projected, PERFORMANCE_CONDITION_ID)
        == ConditionStatus.NOT_SATISFIED
    )


def test_negative_phrase_containing_positive_phrase_is_not_a_false_conflict():
    """“不是永久停业”中的肯定子串不能被重复识别为肯定证据。"""

    projected = make_projector().project(
        QueryState(
            session_id="session.1",
            initial_query="这次不是永久停业，只是设备检修",
        ),
        make_bundle(),
    )

    assert (
        get_condition_status(projected, PERFORMANCE_CONDITION_ID)
        == ConditionStatus.NOT_SATISFIED
    )


def test_conflicting_user_statements_are_preserved():
    """初始陈述与后续明确回答相反时，状态应为CONFLICTING。"""

    state = QueryState(
        session_id="session.1",
        initial_query="健身房已经永久停业",
        dialogue_history=[
            DialogueTurn(
                turn_id=1,
                condition_id=PERFORMANCE_CONDITION_ID,
                question="健身房是否永久停业？",
                answer="并非永久停业，只是暂时关闭",
            )
        ],
    )

    projected = make_projector().project(state, make_bundle())
    condition = next(
        item
        for item in projected.condition_states
        if item.condition_id == PERFORMANCE_CONDITION_ID
    )

    assert condition.status == ConditionStatus.CONFLICTING
    assert len(condition.supporting_fact_ids) == 2


def test_structured_boolean_fact_participates_in_projection():
    """上游已结构化且谓词匹配条件ID的布尔事实应优先作为可靠证据。"""

    state = QueryState(
        session_id="session.1",
        initial_query="我想咨询健身卡退款",
        user_facts=[
            UserFact(
                fact_id="fact.verified.closed",
                text="企业登记状态显示仍在经营",
                predicate=PERFORMANCE_CONDITION_ID,
                value=False,
                source_turn=1,
            )
        ],
    )

    projected = make_projector().project(state, make_bundle())

    assert (
        get_condition_status(projected, PERFORMANCE_CONDITION_ID)
        == ConditionStatus.NOT_SATISFIED
    )


def test_explicit_answer_status_is_used_when_answer_has_no_known_phrase():
    """工作流记录的明确回答状态可以处理不在短语表中的自由回答。"""

    state = QueryState(
        session_id="session.1",
        initial_query="我想咨询退款",
        condition_states=[
            QueryConditionState(
                condition_id=PERFORMANCE_CONDITION_ID,
                status=ConditionStatus.SATISFIED,
                last_updated_turn=1,
            )
        ],
        dialogue_history=[
            DialogueTurn(
                turn_id=1,
                condition_id=PERFORMANCE_CONDITION_ID,
                question="健身房是否还能继续经营？",
                answer="工商登记已经完成注销",
            )
        ],
    )

    projected = make_projector().project(state, make_bundle())
    condition = projected.condition_states[0]

    assert condition.status == ConditionStatus.SATISFIED
    assert condition.supporting_fact_ids == [
        "fact.turn.1.cond.performance_impossible.explicit"
    ]


def test_projection_is_idempotent_and_does_not_duplicate_facts():
    """对同一状态重复投影不能不断追加相同的自动事实。"""

    projector = make_projector()
    initial = QueryState(
        session_id="session.1",
        initial_query="健身房已经永久停业",
    )

    first = projector.project(initial, make_bundle())
    second = projector.project(first, make_bundle())

    assert second.condition_states == first.condition_states
    assert second.user_facts == first.user_facts


def test_conditions_from_unretrieved_rules_are_not_added():
    """投影器只能展开本轮命中规则的条件，不能把整个规则库加入会话。"""

    other_rule = make_rule().model_copy(
        update={
            "rule_id": "rule.other",
            "conditions": [
                RuleCondition(
                    condition_id="cond.unrelated",
                    label="无关条件",
                    predicate="不属于当前候选规则",
                )
            ],
        }
    )
    projector = RuleConditionProjector(
        rules_by_id={
            make_rule().rule_id: make_rule(),
            other_rule.rule_id: other_rule,
        }
    )

    projected = projector.project(
        QueryState(session_id="session.1", initial_query="咨询退款"),
        make_bundle(),
    )

    assert "cond.unrelated" not in {
        item.condition_id for item in projected.condition_states
    }


def test_existing_inactive_condition_is_preserved():
    """其他模块已写入的非当前规则条件不得被投影器删除。"""

    existing = QueryConditionState(
        condition_id="cond.external",
        status=ConditionStatus.UNKNOWN,
    )
    state = QueryState(
        session_id="session.1",
        initial_query="咨询退款",
        condition_states=[existing],
    )

    projected = make_projector().project(state, make_bundle())

    assert projected.condition_states[-1] == existing
