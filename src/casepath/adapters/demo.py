"""P4 核心算法的演示基线。

负责人：P4。P4 可在保持公共 contracts 与 ports 签名兼容的前提下，修改或替换
规则检索、案例检索、条件投影、追问策略和解释规划实现。
本文件不负责会话保存、HTTP API、Neo4j/LLM 连接配置或最终法律判断；当前实现
仅用于联调，不能视为正式 P4-v1 算法。
"""

from __future__ import annotations

from casepath.contracts import (
    CandidateClaim,
    CitationRecord,
    ComparisonBundle,
    ConditionComparison,
    ConditionEvidence,
    ConditionStatus,
    EvidenceAction,
    ExplanationBranch,
    ExplanationPlan,
    QueryConditionState,
    QueryState,
    QuestionCandidate,
    RetrievalBundle,
    RetrievalPath,
    ScoreComponent,
    ScoredReference,
    UserFact,
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
                score_components=[
                    ScoreComponent(name="bm25", value=0.90, explanation="演示关键词得分"),
                    ScoreComponent(name="vector", value=0.92, explanation="演示语义得分"),
                ],
                retrieval_channels=["bm25", "vector"],
                source_span_ids=["span.civil-code.563"],
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
                    score_components=[
                        ScoreComponent(name="text", value=0.84, explanation="演示文本相关性"),
                        ScoreComponent(name="graph_path", value=0.92, explanation="演示规则条件路径"),
                    ],
                    retrieval_channels=["text", "graph"],
                    source_span_ids=["span.case.zeng.reasoning"],
                    graph_paths=[
                        RetrievalPath(
                            node_ids=[
                                DEMO_RULE_ID,
                                "cond.performance_impossible",
                                DEMO_SUPPORT_CASE_ID,
                            ],
                            edge_types=["HAS_CONDITION", "HAS_FINDING"],
                            score=0.92,
                            source_span_ids=["span.case.zeng.reasoning"],
                        )
                    ],
                )
            ],
            limiting_case_refs=[
                ScoredReference(
                    object_id=DEMO_LIMITING_CASE_ID,
                    score=0.61,
                    reasons=["仅用于验证接口；接入前必须替换为真实可溯源案例"],
                    score_components=[
                        ScoreComponent(name="graph_path", value=0.61, explanation="演示限制路径"),
                    ],
                    retrieval_channels=["graph"],
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

    def project(self, state: QueryState, rule_refs: list[ScoredReference]) -> QueryState:
        text = state.initial_query
        existing = {item.condition_id: item for item in state.condition_states}

        patterns: dict[str, tuple[str, ...]] = {
            "cond.contract_exists": ("健身房", "办卡", "服务", "充值"),
            "cond.payment_made": ("充了", "付款", "支付", "交了"),
            "cond.unperformed_balance": ("没消费", "余额", "剩余"),
            "cond.performance_impossible": ("永久停业", "全部门店关闭", "公司注销"),
            "cond.alternative_performance": (),
        }
        matched_text = {
            condition_id: next((word for word in words if word in text), None)
            for condition_id, words in patterns.items()
        }

        states = []
        facts = list(state.user_facts)
        known_fact_ids = {item.fact_id for item in facts}
        for condition_id in self.CONDITION_IDS:
            if condition_id in existing:
                states.append(existing[condition_id])
                continue
            match = matched_text[condition_id]
            status = (
                ConditionStatus.SATISFIED if match is not None else ConditionStatus.UNKNOWN
            )
            fact_id = f"fact.demo.initial.{condition_id.removeprefix('cond.')}"
            if match is not None and fact_id not in known_fact_ids:
                facts.append(UserFact(fact_id=fact_id, text=match, source_turn=0))
                known_fact_ids.add(fact_id)
            states.append(
                QueryConditionState(
                    condition_id=condition_id,
                    status=status,
                    supporting_fact_ids=[fact_id] if match is not None else [],
                    confidence=0.60 if status != ConditionStatus.UNKNOWN else None,
                    evidence=[
                        ConditionEvidence(
                            fact_id=fact_id,
                            relation="SUPPORTS",
                            confidence=0.60,
                            reason="Demo关键词与条件标签匹配",
                        )
                    ]
                    if match is not None
                    else [],
                    mapping_reasons=["Demo关键词条件投影；正式P4实现必须替换"],
                    score_components=[
                        ScoreComponent(
                            name="keyword_baseline",
                            value=0.60 if status != ConditionStatus.UNKNOWN else 0.0,
                            explanation="仅用于接口联调",
                        )
                    ],
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
        return state.model_copy(
            update={"user_facts": facts, "candidate_claims": claims, "condition_states": states}
        )


class DemoCaseComparator:
    """固定分值仅验证共享分化结果的数据流，不代表真实统计结论。"""

    def compare(self, state: QueryState, bundle: RetrievalBundle) -> ComparisonBundle:
        condition_id = "cond.performance_impossible"
        comparison = ConditionComparison(
            condition_id=condition_id,
            status_counts={
                ConditionStatus.SATISFIED: len(bundle.support_case_refs),
                ConditionStatus.NOT_SATISFIED: len(bundle.limiting_case_refs),
                ConditionStatus.UNKNOWN: len(bundle.boundary_case_refs),
            },
            condition_entropy=0.91,
            outcome_mutual_information=0.74,
            expected_information_gain=0.81,
            expected_rank_change=0.68,
            expected_branch_reduction=0.76,
            case_disagreement_score=0.86,
            rule_condition_discriminativeness=0.88,
            support_case_ids=[ref.object_id for ref in bundle.support_case_refs],
            limiting_case_ids=[ref.object_id for ref in bundle.limiting_case_refs],
            boundary_case_ids=[ref.object_id for ref in bundle.boundary_case_refs],
            score_components=[
                ScoreComponent(
                    name="demo_comparison",
                    value=0.86,
                    explanation="固定演示分值，正式P4实现必须替换",
                )
            ],
        )
        return ComparisonBundle(
            condition_comparisons=[comparison],
            ranked_condition_ids=[condition_id],
            degraded=True,
            degradation_reason="案例分化指标为固定演示值。",
        )


class DemoQuestionPolicy:
    def select(
        self,
        state: QueryState,
        bundle: RetrievalBundle,
        comparison: ComparisonBundle,
    ) -> QuestionCandidate | None:
        by_id = {item.condition_id: item.status for item in state.condition_states}
        asked_question_ids = {turn.question_id for turn in state.dialogue_history}
        asked_condition_ids = {turn.condition_id for turn in state.dialogue_history}
        question_id = "question.performance_impossible.1"
        condition_id = "cond.performance_impossible"
        # Demo 也遵守端口约定：已问条件由策略过滤，工作流只保留错误检测。
        if (
            by_id.get(condition_id) == ConditionStatus.UNKNOWN
            and question_id not in asked_question_ids
            and condition_id not in asked_condition_ids
        ):
            return QuestionCandidate(
                question_id=question_id,
                condition_id=condition_id,
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
                boundary_case_ids=[ref.object_id for ref in bundle.boundary_case_refs],
            )
        return None


class DemoExplanationPlanner:
    def build(
        self,
        state: QueryState,
        bundle: RetrievalBundle,
        comparison: ComparisonBundle,
    ) -> ExplanationPlan:
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
            boundary_case_ids=[ref.object_id for ref in bundle.boundary_case_refs],
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
