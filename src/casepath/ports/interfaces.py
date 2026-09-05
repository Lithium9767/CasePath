"""P1 维护的 P4 算法接入端口。

P1 负责稳定输入输出签名，P4 负责提供具体算法实现。P4 如需调整端口或公共合同，
应先与 P1 协调并进行版本化修改，不能绕过端口向工作流传递自由结构状态。
"""

from __future__ import annotations

from typing import Protocol

from casepath.contracts import (
    AnswerInterpretation,
    AnswerRequest,
    ExplanationPlan,
    QueryState,
    QuestionCandidate,
    RetrievalBundle,
    ScoredReference,
)


class RuleRetriever(Protocol):
    def retrieve(self, state: QueryState) -> list[ScoredReference]: ...


class CaseRetriever(Protocol):
    def retrieve(self, state: QueryState, rule_refs: list[ScoredReference]) -> RetrievalBundle: ...


class ConditionProjector(Protocol):
    def project(self, state: QueryState, bundle: RetrievalBundle) -> QueryState: ...


class QuestionPolicy(Protocol):
    """选择下一条问题；实现必须过滤对话历史中已回答的问题和条件。"""

    def select(self, state: QueryState, bundle: RetrievalBundle) -> QuestionCandidate | None: ...


class ExplanationPlanner(Protocol):
    def build(self, state: QueryState, bundle: RetrievalBundle) -> ExplanationPlan: ...


class AnswerInterpreter(Protocol):
    """P1 定义调用约定，P4 实现回答到事实及条件状态的语义映射。"""

    def interpret(
        self,
        state: QueryState,
        pending_question: QuestionCandidate,
        answer_request: AnswerRequest,
    ) -> AnswerInterpretation:
        """事实须摘自本轮回答；新事实和条件更新须标记下一轮 turn_id。"""
        ...
