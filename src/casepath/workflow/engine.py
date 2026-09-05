"""P1 维护的单轮工作流编排。

本文件只负责端口调用顺序、追问预算和输出一致性，不实现 P4 的混合召回、
条件语义映射、图路径评分、案例分化检测或信息增益算法。
"""

from __future__ import annotations

from dataclasses import dataclass

from casepath.contracts import (
    QueryState,
    SessionStatus,
    WorkflowSnapshot,
)
from casepath.ports import (
    CaseComparator,
    CaseRetriever,
    ConditionProjector,
    ExplanationPlanner,
    QuestionPolicy,
    RuleRetriever,
)


class WorkflowInvariantError(RuntimeError):
    """工作流依赖返回了违反编排约定的结果。"""


@dataclass(frozen=True)
class WorkflowDependencies:
    rule_retriever: RuleRetriever
    condition_projector: ConditionProjector
    case_retriever: CaseRetriever
    case_comparator: CaseComparator
    question_policy: QuestionPolicy
    explanation_planner: ExplanationPlanner


class CasePathWorkflow:
    """Deterministic orchestration; adapters may use databases or LLMs behind ports."""

    def __init__(self, dependencies: WorkflowDependencies, *, max_question_turns: int = 3) -> None:
        # 轮数上限是交互预算，不意味着未知条件已经满足。
        if max_question_turns < 0:
            raise ValueError("max_question_turns must be non-negative")
        self.dependencies = dependencies
        self.max_question_turns = max_question_turns

    def run(self, state: QueryState) -> WorkflowSnapshot:
        # 日志只记录真实调用，不将未实现的解析、案例分化或引用核验写成已完成。
        trace = []
        rule_refs = self.dependencies.rule_retriever.retrieve(state)
        trace.append("RETRIEVE_RULES")

        projected = self.dependencies.condition_projector.project(state, rule_refs)
        trace.append("PROJECT_QUERY")

        bundle = self.dependencies.case_retriever.retrieve(projected, rule_refs)
        trace.append("RETRIEVE_CASES")

        comparison = self.dependencies.case_comparator.compare(projected, bundle)
        trace.append("COMPARE_CASES")

        if len(projected.dialogue_history) >= self.max_question_turns:
            question = None
            trace.append("QUESTION_BUDGET_REACHED")
        else:
            question = self.dependencies.question_policy.select(projected, bundle, comparison)
            # 策略负责过滤历史；这里仅守住“不能重复发问”的系统不变量。
            if question is not None and any(
                turn.question_id == question.question_id
                or turn.condition_id == question.condition_id
                for turn in projected.dialogue_history
            ):
                raise WorkflowInvariantError("question policy selected an answered question")
        if question is None:
            projected = projected.model_copy(update={"status": SessionStatus.READY_TO_EXPLAIN})
            trace.append("STOP_CLARIFICATION")
        else:
            projected = projected.model_copy(update={"status": SessionStatus.NEEDS_CLARIFICATION})
            trace.append("SCORE_QUESTIONS")

        plan = self.dependencies.explanation_planner.build(projected, bundle, comparison)
        trace.append("BUILD_EXPLANATION_PLAN")
        return WorkflowSnapshot(
            query_state=projected,
            retrieval_bundle=bundle,
            comparison_bundle=comparison,
            next_question=question,
            explanation_plan=plan,
            trace=trace,
        )
