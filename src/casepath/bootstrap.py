"""P1 维护的组件装配入口。

P1 在此选择演示或正式实现，并接入 Neo4j、LLM 等基础设施；P4 提供符合 ports
约定的算法对象，不在本文件中实现评分公式或法律语义判断。
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from typing import Protocol

from casepath.adapters import (
    DemoCaseComparator,
    DemoCaseRetriever,
    DemoConditionProjector,
    DemoExplanationPlanner,
    DemoQuestionPolicy,
    DemoRuleRetriever,
    Neo4jGraphGateway,
    OpenAICompatibleStructuredLanguageModel,
)
from casepath.adapters.demo_answer_interpreter import DemoAnswerInterpreter
from casepath.adapters.memory_session_repository import InMemorySessionRepository
from casepath.application.errors import GraphUnavailable, LanguageModelUnavailable
from casepath.application.session_service import SessionService
from casepath.config import RuntimeMode, RuntimeSettings
from casepath.contracts import CapabilityMode, CapabilityStatus
from casepath.ports import (
    AnswerInterpreter,
    LegalGraphGateway,
    ManagedLegalGraphGateway,
    ManagedStructuredLanguageModel,
    StructuredLanguageModel,
)
from casepath.ports.session_repository import SessionRepository
from casepath.workflow import CasePathWorkflow, WorkflowDependencies


@dataclass(frozen=True)
class P4Components:
    """P4交给P1装配的完整组件包；P1不检查其内部算法类型。"""

    workflow_dependencies: WorkflowDependencies
    answer_interpreter: AnswerInterpreter | None
    capabilities: tuple[CapabilityStatus, ...]


class P4ComponentFactory(Protocol):
    """P4实现此工厂，显式接收P1管理的基础设施端口。"""

    def build(
        self,
        *,
        graph_gateway: LegalGraphGateway | None,
        language_model: StructuredLanguageModel | None,
    ) -> P4Components: ...


@dataclass
class LiveRuntime:
    """正式服务及其受P1管理的连接资源。"""

    service: SessionService
    repository_capability: CapabilityStatus
    p4_capabilities: tuple[CapabilityStatus, ...]
    graph_gateway: ManagedLegalGraphGateway | None
    language_model: ManagedStructuredLanguageModel | None
    graph_initialization_failed: bool = False
    llm_initialization_failed: bool = False
    _closed: bool = False

    def capabilities(self) -> list[CapabilityStatus]:
        """每次请求重新探测Neo4j；LLM只报告配置状态，避免健康检查产生费用。"""

        result = [self.repository_capability.model_copy(deep=True)]
        result.extend(item.model_copy(deep=True) for item in self.p4_capabilities)
        if self.graph_gateway is None:
            graph_reason = (
                "Neo4j配置完整，但驱动初始化失败。"
                if self.graph_initialization_failed
                else "未提供完整Neo4j配置。"
            )
            result.append(
                CapabilityStatus(
                    capability="legal_graph",
                    available=False,
                    mode=CapabilityMode.DISABLED,
                    degraded=True,
                    reason=graph_reason,
                )
            )
        else:
            try:
                self.graph_gateway.verify_connectivity()
            except GraphUnavailable:
                result.append(
                    CapabilityStatus(
                        capability="legal_graph",
                        available=False,
                        mode=CapabilityMode.LIVE,
                        degraded=True,
                        reason="Neo4j当前不可连接；查询会返回稳定的不可用错误。",
                    )
                )
            else:
                result.append(
                    CapabilityStatus(
                        capability="legal_graph",
                        available=True,
                        mode=CapabilityMode.LIVE,
                        reason="Neo4j连接探测成功。",
                    )
                )
        if self.language_model is None:
            llm_reason = (
                "LLM配置完整，但客户端初始化失败。"
                if self.llm_initialization_failed
                else "未提供LLM API Key。"
            )
            result.append(
                CapabilityStatus(
                    capability="llm",
                    available=False,
                    mode=CapabilityMode.DISABLED,
                    degraded=True,
                    reason=llm_reason,
                )
            )
        else:
            result.append(
                CapabilityStatus(
                    capability="llm",
                    available=True,
                    mode=CapabilityMode.LIVE,
                    reason="客户端配置已加载；实际可用性在每次调用时验证。",
                )
            )
        if not any(item.capability == "citation_verification" for item in result):
            result.append(
                CapabilityStatus(
                    capability="citation_verification",
                    available=False,
                    mode=CapabilityMode.DISABLED,
                    reason="尚未接入引用核验器。",
                )
            )
        return result

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self.language_model is not None:
                self.language_model.close()
        finally:
            try:
                if self.graph_gateway is not None:
                    self.graph_gateway.close()
            finally:
                # 即使某个客户端关闭失败，也要把运行时标记为已关闭，避免重复释放。
                self._closed = True


def _build_graph_gateway(settings: RuntimeSettings) -> ManagedLegalGraphGateway:
    password = settings.neo4j_password
    if not settings.neo4j_configured or password is None:
        raise ValueError("Neo4j配置不完整")
    return Neo4jGraphGateway.connect(
        uri=settings.neo4j_uri or "",
        user=settings.neo4j_user or "",
        password=password.get_secret_value(),
        database=settings.neo4j_database,
        connection_timeout_seconds=settings.neo4j_connection_timeout_seconds,
        query_timeout_seconds=settings.neo4j_query_timeout_seconds,
        max_retry_seconds=settings.neo4j_max_retry_seconds,
    )


def _build_language_model(settings: RuntimeSettings) -> ManagedStructuredLanguageModel:
    api_key = settings.llm_api_key
    if not settings.llm_configured or api_key is None:
        raise ValueError("LLM配置不完整")
    return OpenAICompatibleStructuredLanguageModel.connect(
        api_key=api_key.get_secret_value(),
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        max_completion_tokens=settings.llm_max_completion_tokens,
        output_mode=settings.llm_structured_output,
    )


def build_live_runtime(
    settings: RuntimeSettings,
    p4_factory: P4ComponentFactory,
    *,
    repository: SessionRepository | None = None,
    repository_capability: CapabilityStatus | None = None,
    graph_factory=_build_graph_gateway,
    language_model_factory=_build_language_model,
) -> LiveRuntime:
    """构造正式运行时；基础设施失败不会触发Demo回退，也不会阻止API创建。"""

    if settings.mode != RuntimeMode.LIVE:
        raise ValueError("build_live_runtime requires CASEPATH_MODE=live")
    if settings.llm_required and not settings.llm_configured:
        raise LanguageModelUnavailable("已要求 LLM，但未配置 CASEPATH_LLM_API_KEY 或 DASHSCOPE_API_KEY")
    # ExitStack确保P4工厂或后续合同校验失败时，已经建立的外部连接不会泄漏。
    with ExitStack() as cleanup:
        graph_gateway = None
        graph_failed = False
        if settings.neo4j_configured:
            try:
                graph_gateway = graph_factory(settings)
            except GraphUnavailable:
                graph_failed = True
            else:
                cleanup.callback(graph_gateway.close)
        language_model = None
        llm_failed = False
        if settings.llm_configured:
            try:
                language_model = language_model_factory(settings)
            except LanguageModelUnavailable:
                if settings.llm_required:
                    # ExitStack 仍会关闭此前创建的图客户端，不静默退化为词法模式。
                    raise
                llm_failed = True
            else:
                cleanup.callback(language_model.close)

        components = p4_factory.build(
            graph_gateway=graph_gateway,
            language_model=language_model,
        )
        reserved_capabilities = {"session_repository", "legal_graph", "llm"}
        component_names = [item.capability for item in components.capabilities]
        if len(component_names) != len(set(component_names)):
            raise ValueError("P4 capability names must be unique")
        if reserved_capabilities & set(component_names):
            raise ValueError("P4 capabilities cannot replace P1 infrastructure capabilities")

        if repository is None:
            repository = InMemorySessionRepository()
            repository_capability = CapabilityStatus(
                capability="session_repository",
                available=True,
                mode=CapabilityMode.MEMORY,
                degraded=True,
                reason="正式算法使用单进程内存会话；重启后数据丢失。",
            )
        elif repository_capability is None:
            raise ValueError("an injected repository requires an explicit capability status")

        workflow = CasePathWorkflow(
            components.workflow_dependencies,
            max_question_turns=settings.max_question_turns,
        )
        service = build_session_service(
            workflow=workflow,
            repository=repository,
            answer_interpreter=components.answer_interpreter,
        )
        runtime = LiveRuntime(
            service=service,
            repository_capability=repository_capability,
            p4_capabilities=components.capabilities,
            graph_gateway=graph_gateway,
            language_model=language_model,
            graph_initialization_failed=graph_failed,
            llm_initialization_failed=llm_failed,
        )
        cleanup.pop_all()
        return runtime


def build_session_service(
    *,
    workflow: CasePathWorkflow,
    repository: SessionRepository,
    answer_interpreter: AnswerInterpreter | None,
) -> SessionService:
    """注入正式或测试组件，不在此创建Neo4j/LLM凭据或偷偷回退Demo。"""
    return SessionService(
        repository=repository,
        workflow=workflow,
        answer_interpreter=answer_interpreter,
    )


def build_demo_workflow() -> CasePathWorkflow:
    return CasePathWorkflow(
        WorkflowDependencies(
            rule_retriever=DemoRuleRetriever(),
            condition_projector=DemoConditionProjector(),
            case_retriever=DemoCaseRetriever(),
            case_comparator=DemoCaseComparator(),
            question_policy=DemoQuestionPolicy(),
            explanation_planner=DemoExplanationPlanner(),
        )
    )


def build_demo_session_service() -> SessionService:
    """每个应用实例只调用一次；正式 P4 接入时替换解释器和工作流依赖。"""
    return build_session_service(
        workflow=build_demo_workflow(),
        repository=InMemorySessionRepository(),
        answer_interpreter=DemoAnswerInterpreter(),
    )


def build_demo_capabilities() -> list[CapabilityStatus]:
    """只报告真实装配情况，进程健康不等于算法或引用核验可用。"""
    capabilities = [
        CapabilityStatus(
            capability="session_repository", available=True, mode=CapabilityMode.MEMORY,
            reason="单进程内存存储，重启后会话丢失；不支持多个 worker。",
        )
    ]
    demo_reasons = {
        "rule_retriever": "返回固定候选规则，未连接正式索引。",
        "condition_projector": "仅使用关键词投影，未执行正式语义桥接。",
        "case_retriever": "包含占位限制案例，未连接正式案例库。",
        "case_comparator": "分化指标为固定演示值，未执行统计计算。",
        "question_policy": "仅包含一个固定追问模板。",
        "explanation_planner": "仅生成固定条件化解释模板。",
        "answer_interpreter": "只保留回答原文，不执行真实条件语义映射。",
    }
    for name, reason in demo_reasons.items():
        capabilities.append(CapabilityStatus(
            capability=name, available=True, mode=CapabilityMode.DEMO, degraded=True,
            reason=reason,
        ))
    for name in ("legal_graph", "llm", "citation_verification"):
        capabilities.append(CapabilityStatus(
            capability=name, available=False, mode=CapabilityMode.DISABLED,
            reason="尚未接入，不能将结果理解为已核验的法律结论。",
        ))
    return capabilities
