from __future__ import annotations

from casepath.contracts import (
    CandidateClaim,
    CitationRecord,
    ConditionStatus,
    EvidenceAction,
    ExplanationBranch,
    ExplanationPlan,
    QueryConditionState,
    QueryState,
    QuestionCandidate,
    RetrievalBundle,
    ScoreComponent,
    ScoredReference,
)

DEMO_RULE_ID = "rule.service_contract.termination_refund.v1"
DEMO_SUPPORT_CASE_ID = "case.zeng-v-wuhan-fitness.2021"
DEMO_LIMITING_CASE_ID = "case.demo-limiting.placeholder"


class DemoRuleRetriever:
    def retrieve(self, state: QueryState) -> list[ScoredReference]:
        return [
            ScoredReference(
                object_id=DEMO_RULE_ID,
                score=0.91,
                reasons=["用户请求返还未消费余额", "描述涉及服务合同无法继续履行"],
            )
        ]


class DemoCaseRetriever:
    def retrieve(self, state: QueryState, rule_refs: list[ScoredReference]) -> RetrievalBundle:
        return RetrievalBundle(
            rule_refs=rule_refs,
            support_case_refs=[
                ScoredReference(
                    object_id=DEMO_SUPPORT_CASE_ID,
                    score=0.88,
                    reasons=["服务合同", "履行地点变化", "解除请求"],
                )
            ],
            limiting_case_refs=[
                ScoredReference(
                    object_id=DEMO_LIMITING_CASE_ID,
                    score=0.61,
                    reasons=["仅用于验证接口；接入前必须替换为真实可溯源案例"],
                )
            ],
            cited_span_ids=["span.civil-code.563", "span.case.zeng.reasoning"],
            degraded=True,
            degradation_reason="限制性案例尚未接入真实数据，当前结果仅用于接口演示。",
        )


class DemoConditionProjector:
    CONDITION_IDS = (
        "cond.contract_exists",
        "cond.payment_made",
        "cond.unperformed_balance",
        "cond.performance_impossible",
        "cond.alternative_performance",
    )

    def project(self, state: QueryState, bundle: RetrievalBundle) -> QueryState:
        text = state.initial_query
        existing = {item.condition_id: item for item in state.condition_states}

        inferred: dict[str, ConditionStatus] = {
            "cond.contract_exists": ConditionStatus.SATISFIED
            if any(word in text for word in ("健身房", "办卡", "服务", "充值"))
            else ConditionStatus.UNKNOWN,
            "cond.payment_made": ConditionStatus.SATISFIED
            if any(word in text for word in ("充了", "付款", "支付", "交了"))
            else ConditionStatus.UNKNOWN,
            "cond.unperformed_balance": ConditionStatus.SATISFIED
            if any(word in text for word in ("没消费", "余额", "剩余"))
            else ConditionStatus.UNKNOWN,
            "cond.performance_impossible": ConditionStatus.SATISFIED
            if any(word in text for word in ("永久停业", "全部门店关闭", "公司注销"))
            else ConditionStatus.UNKNOWN,
            "cond.alternative_performance": ConditionStatus.UNKNOWN,
        }

        states = []
        for condition_id in self.CONDITION_IDS:
            states.append(
                existing.get(condition_id)
                or QueryConditionState(
                    condition_id=condition_id,
                    status=inferred[condition_id],
                    last_updated_turn=0,
                )
            )

        claims = state.candidate_claims or [
            CandidateClaim(
                claim_type="服务合同解除与返还",
                requested_remedy="解除合同并返还未消费余额",
                confidence=0.82,
            )
        ]
        return state.model_copy(update={"candidate_claims": claims, "condition_states": states})


class DemoQuestionPolicy:
    def select(self, state: QueryState, bundle: RetrievalBundle) -> QuestionCandidate | None:
        by_id = {item.condition_id: item.status for item in state.condition_states}
        if by_id.get("cond.performance_impossible") == ConditionStatus.UNKNOWN:
            return QuestionCandidate(
                question_id="question.performance_impossible.1",
                condition_id="cond.performance_impossible",
                question="健身房是永久停止经营，还是暂时关闭？",
                why_asked="是否已经无法继续履行，会改变解除和返还规则的解释分支。",
                options=["永久停止经营", "暂时关闭", "仍可在其他门店使用", "不清楚"],
                utility=0.87,
                score_components=[
                    ScoreComponent(name="rule_centrality", value=0.95, explanation="必要条件"),
                    ScoreComponent(name="case_contrast", value=0.86, explanation="正反案例分化"),
                    ScoreComponent(name="answerability", value=0.80, explanation="用户可观察"),
                ],
                supporting_case_ids=[DEMO_SUPPORT_CASE_ID],
                limiting_case_ids=[DEMO_LIMITING_CASE_ID],
            )
        return None


class DemoExplanationPlanner:
    def build(self, state: QueryState, bundle: RetrievalBundle) -> ExplanationPlan:
        statuses = {item.condition_id: item.status for item in state.condition_states}
        unresolved = [key for key, value in statuses.items() if value == ConditionStatus.UNKNOWN]
        impossible = statuses.get("cond.performance_impossible")
        if impossible == ConditionStatus.SATISFIED:
            main = "当前事实支持进一步分析服务合同解除及返还未消费余额。"
        else:
            main = "当前仍需确认经营者是否已经无法继续履行，才能稳定解释解除与返还路径。"

        return ExplanationPlan(
            session_id=state.session_id,
            main_explanation=main,
            candidate_claims=[claim.claim_type for claim in state.candidate_claims],
            applicable_rule_ids=[ref.object_id for ref in bundle.rule_refs],
            support_case_ids=[ref.object_id for ref in bundle.support_case_refs],
            limiting_case_ids=[ref.object_id for ref in bundle.limiting_case_refs],
            conditional_branches=[
                ExplanationBranch(
                    branch_id="branch.performance_impossible",
                    condition="如果经营者永久停业且没有替代履行安排",
                    explanation="该事实更支持进入解除并返还未消费余额的解释路径。",
                    citation_ids=["citation.civil-code.563"],
                )
            ],
            unresolved_condition_ids=unresolved,
            evidence_actions=[
                EvidenceAction(
                    action_id="evidence.business_status",
                    description="保存停业通知、聊天记录及经营主体状态信息。",
                    related_condition_ids=["cond.performance_impossible"],
                )
            ],
            citations=[
                CitationRecord(
                    citation_id="citation.civil-code.563",
                    source_span_ids=["span.civil-code.563"],
                    supports="合同解除规则候选依据",
                    verified=False,
                )
            ],
        )
