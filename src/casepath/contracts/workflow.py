from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel
from .explanation import ExplanationPlan
from .query import QueryState, QuestionCandidate
from .retrieval import ComparisonBundle, RetrievalBundle


class WorkflowSnapshot(ContractModel): # 一次CasePath工作流运行后返回给P5的完整快照
    contract_version: Literal["1.1", "1.3"] = "1.3" # 兼容读取v1.1，新结果默认v1.3
    query_state: QueryState # 用户事实、条件状态和历史对话的当前状态
    retrieval_bundle: RetrievalBundle # 本轮检索到的规则、支持案例、限制案例和边界案例
    comparison_bundle: ComparisonBundle | None = None # P4-v1条件分化指标；v1.1历史数据允许为空
    next_question: QuestionCandidate | None = None # 下一条高价值追问；无需继续追问时为空
    explanation_plan: ExplanationPlan # 基于当前信息形成的结构化法律解释计划
    trace: list[str] = Field(default_factory=list) # 工作流本轮经过的步骤，便于调试和回放

    @model_validator(mode="after")
    def validate_snapshot_consistency(self) -> WorkflowSnapshot:
        if self.contract_version == "1.3" and self.comparison_bundle is None:
            raise ValueError("v1.3 workflow snapshot requires a comparison bundle")
        # 查询状态与解释计划必须属于同一个用户会话。
        if self.query_state.session_id != self.explanation_plan.session_id:
            raise ValueError("query state and explanation plan must use the same session_id")

        condition_ids = {item.condition_id for item in self.query_state.condition_states}
        support_case_ids = {item.object_id for item in self.retrieval_bundle.support_case_refs}
        limiting_case_ids = {item.object_id for item in self.retrieval_bundle.limiting_case_refs}
        boundary_case_ids = {item.object_id for item in self.retrieval_bundle.boundary_case_refs}
        rule_ids = {item.object_id for item in self.retrieval_bundle.rule_refs}

        if self.comparison_bundle is not None:
            for item in self.comparison_bundle.condition_comparisons:
                if item.condition_id not in condition_ids:
                    raise ValueError("comparison references an unknown query condition")
                role_references = (
                    (item.support_case_ids, support_case_ids),
                    (item.limiting_case_ids, limiting_case_ids),
                    (item.boundary_case_ids, boundary_case_ids),
                )
                if any(set(references) - known for references, known in role_references):
                    raise ValueError("comparison references a case outside its retrieval role")

        # 存在下一条追问时，它必须引用当前条件矩阵中已经存在的条件。
        if self.next_question is not None:
            if self.next_question.condition_id not in condition_ids:
                raise ValueError("next question references an unknown query condition")
            question_roles = (
                (self.next_question.supporting_case_ids, support_case_ids),
                (self.next_question.limiting_case_ids, limiting_case_ids),
                (self.next_question.boundary_case_ids, boundary_case_ids),
            )
            if any(set(references) - known for references, known in question_roles):
                raise ValueError("next question references a case outside its retrieval role")

        plan_roles = (
            (self.explanation_plan.applicable_rule_ids, rule_ids),
            (self.explanation_plan.support_case_ids, support_case_ids),
            (self.explanation_plan.limiting_case_ids, limiting_case_ids),
            (self.explanation_plan.boundary_case_ids, boundary_case_ids),
        )
        if any(set(references) - known for references, known in plan_roles):
            raise ValueError("explanation plan references an object outside retrieval results")
        return self
