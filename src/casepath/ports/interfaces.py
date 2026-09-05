"""P1 维护的 P4 算法接入端口。

P1 负责稳定输入输出签名，P4 负责提供具体算法实现。P4 如需调整端口或公共合同，
应先与 P1 协调并进行版本化修改，不能绕过端口向工作流传递自由结构状态。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, TypeVar

from casepath.contracts import (
    AnswerInterpretation,
    AnswerRequest,
    ComparisonBundle,
    ContractModel,
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
    """将用户事实投影到候选规则的条件空间，必须先于案例检索执行。"""

    def project(self, state: QueryState, rule_refs: list[ScoredReference]) -> QueryState: ...


class CaseComparator(Protocol):
    """计算共享分化指标，QuestionPolicy和ExplanationPlanner不得各算一套。"""

    def compare(self, state: QueryState, bundle: RetrievalBundle) -> ComparisonBundle: ...


class QuestionPolicy(Protocol):
    """选择下一条问题；实现必须过滤对话历史中已回答的问题和条件。"""

    def select(
        self,
        state: QueryState,
        bundle: RetrievalBundle,
        comparison: ComparisonBundle,
    ) -> QuestionCandidate | None: ...


class ExplanationPlanner(Protocol):
    def build(
        self,
        state: QueryState,
        bundle: RetrievalBundle,
        comparison: ComparisonBundle,
    ) -> ExplanationPlan: ...


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


class LegalGraphGateway(Protocol):
    """P1管理连接生命周期；P4提供只读Cypher和参数，不直接持有凭据。"""

    def execute_read(
        self,
        query: str,
        parameters: Mapping[str, object],
        *,
        timeout_seconds: float | None = None,
    ) -> list[Mapping[str, object]]: ...


ContractT = TypeVar("ContractT", bound=ContractModel)


class StructuredLanguageModel(Protocol):
    """只允许结构化合同输出；SDK、密钥、超时和重试由P1装配层管理。"""

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ContractT],
        timeout_seconds: float | None = None,
    ) -> ContractT: ...
