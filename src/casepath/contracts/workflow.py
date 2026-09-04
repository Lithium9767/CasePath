from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel
from .explanation import ExplanationPlan
from .query import QueryState, QuestionCandidate
from .retrieval import RetrievalBundle


class WorkflowSnapshot(ContractModel): # 一次CasePath工作流运行后返回给P5的完整快照
    contract_version: Literal["1.1"] = "1.1" # 工作流快照合同版本号，只接受1.1
    query_state: QueryState # 用户事实、条件状态和历史对话的当前状态
    retrieval_bundle: RetrievalBundle # 本轮检索到的规则、支持案例、限制案例和边界案例
    next_question: QuestionCandidate | None = None # 下一条高价值追问；无需继续追问时为空
    explanation_plan: ExplanationPlan # 基于当前信息形成的结构化法律解释计划
    trace: list[str] = Field(default_factory=list) # 工作流本轮经过的步骤，便于调试和回放

    @model_validator(mode="after")
    def validate_snapshot_consistency(self) -> WorkflowSnapshot:
        # 查询状态与解释计划必须属于同一个用户会话。
        if self.query_state.session_id != self.explanation_plan.session_id:
            raise ValueError("query state and explanation plan must use the same session_id")

        # 存在下一条追问时，它必须引用当前条件矩阵中已经存在的条件。
        if self.next_question is not None:
            condition_ids = {item.condition_id for item in self.query_state.condition_states}
            if self.next_question.condition_id not in condition_ids:
                raise ValueError("next question references an unknown query condition")
        return self
