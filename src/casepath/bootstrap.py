"""P1 正式运行时装配入口（Live）。

离线 Demo 装配见 `casepath.adapters.demo`（`build_demo_*`）。
本文件只负责 Neo4j/LLM 注入与 `build_live_runtime`。
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from typing import Protocol

from casepath.adapters import (
    Neo4jGraphGateway,
    OpenAICompatibleStructuredLanguageModel,
)
from casepath.adapters.infrastructure.memory_session_repository import InMemorySessionRepository
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
    # 【接口定义】约定工厂必须有 build 方法；不在这里实现算法，实际对象由 P4 提供。
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
        # 【能力查询链】供 /v1/capabilities 调用，不参与 A-E 评分。
        # 能力可配置、服务可连接与算法已完整实现是三回事，不能只看一个 LIVE 标记。
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
    # 【图客户端默认工厂】graph_factory(settings) 默认进入本函数。
    # settings 是已解析的配置对象，不在这里重新读取 .env。
    password = settings.neo4j_password
    if not settings.neo4j_configured or password is None:
        raise ValueError("Neo4j配置不完整")
    return Neo4jGraphGateway.connect(
        # 下一站 adapters/infrastructure/neo4j_gateway.py 的 connect 类方法。
        # 返回包装 Neo4j 驱动的网关对象，不返回案例，也不在这里执行 Cypher。
        uri=settings.neo4j_uri or "",
        user=settings.neo4j_user or "",
        password=password.get_secret_value(),
        database=settings.neo4j_database,
        connection_timeout_seconds=settings.neo4j_connection_timeout_seconds,
        query_timeout_seconds=settings.neo4j_query_timeout_seconds,
        max_retry_seconds=settings.neo4j_max_retry_seconds,
    )


def _build_language_model(settings: RuntimeSettings) -> ManagedStructuredLanguageModel:
    # 【LLM 默认工厂】language_model_factory(settings) 默认进入本函数。
    # 这里创建聊天模型客户端，和 configured.py 创建的 Embedding 客户端是两回事。
    api_key = settings.llm_api_key
    if not settings.llm_configured or api_key is None:
        raise ValueError("LLM配置不完整")
    return OpenAICompatibleStructuredLanguageModel.connect(
        # 下一站 adapters/infrastructure/structured_llm.py 的 connect 类方法。
        # 设置模型、超时、重试和各阶段预算；此时没有 prompt，不调用生成接口。
        api_key=api_key.get_secret_value(),
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        max_completion_tokens=settings.llm_max_completion_tokens,
        output_mode=settings.llm_structured_output,
        stage_policies=settings.llm_stage_policies,
    )


def build_live_runtime(
    settings: RuntimeSettings,
    p4_factory: P4ComponentFactory,
    *,
    repository: SessionRepository | None = None,
    repository_capability: CapabilityStatus | None = None,
    graph_factory=_build_graph_gateway,
    language_model_factory=_build_language_model,
    # 上面两个默认值是“函数对象”，不是函数执行结果；测试可传入替身函数。
    # 下文加上 (settings) 才真正调用，创建各自的客户端对象。
) -> LiveRuntime:
    # 【跨文件跳转 3】调用方是 api/main.py 的 create_live_app，不是 configured.py 直接调用。
    # 完整入口链：configured.create_configured_app → main.create_live_app → 本函数。
    # 下文创建图/LLM 客户端，再调用 p4_factory.build，最后返回 LiveRuntime 给 main.py。
    """构造正式运行时；基础设施失败不会触发Demo回退，也不会阻止API创建。"""

    # 只能在live模式使用
    if settings.mode != RuntimeMode.LIVE:
        raise ValueError("build_live_runtime requires CASEPATH_MODE=live")
    # 如果要求LLM，但未配置 CASEPATH_LLM_API_KEY 或 DASHSCOPE_API_KEY，则抛出异常
    if settings.llm_required and not settings.llm_configured:
        raise LanguageModelUnavailable("已要求 LLM，但未配置 CASEPATH_LLM_API_KEY 或 DASHSCOPE_API_KEY")
    # ExitStack确保P4工厂或后续合同校验失败时，已经建立的外部连接不会泄漏。
    # 从这里开始创建的外部连接，如果后面任何一步失败， ExitStack负责把已经创建成功的连接关掉
    with ExitStack() as cleanup:
        # 【启动装配】先创建基础设施，再交给工厂。异常时释放已建连接，不是重跑问答。
        graph_gateway = None
        graph_failed = False
        # 如果Neo4j配置完整，则创建图客户端
        if settings.neo4j_configured:
            try:
                graph_gateway = graph_factory(settings)
                # 默认跳转：_build_graph_gateway → Neo4jGraphGateway.connect。
                # 此变量保存客户端对象；后面交给 P4 工厂，再由 Neo4jGraphNavigator 使用。
                # 驱动构造成功不代表数据库已连通；能力探测或实际查询才验证网络连接。
            except GraphUnavailable:
                graph_failed = True
            else:
                # 如果后面失败，ExitStack 记得调用 graph_gateway.close()
                cleanup.callback(graph_gateway.close)
        language_model = None
        # 如果LLM配置完整，则创建LLM客户端
        llm_failed = False
        if settings.llm_configured:
            try:
                language_model = language_model_factory(settings)
                # 默认跳转：_build_language_model → OpenAICompatibleStructuredLanguageModel.connect。
                # 此变量不是模型生成的答案，而是后续可调用 generate_structured 的适配器。
                # 密钥或模型是否真正可用，要到 P4 提交实际生成请求时才能验证。
            except LanguageModelUnavailable:
                if settings.llm_required:
                    # ExitStack 仍会关闭此前创建的图客户端，不静默退化为词法模式。
                    raise
                llm_failed = True
            else:
                cleanup.callback(language_model.close)

        components = p4_factory.build(
            # 【启动 4】跳到 adapters/p4_factory.py 的 HybridCypherP4Factory.build。
            # 它返回 P4Components：六个单轮算法对象、一个回答解释器、一份能力清单。
            # build 返回后继续下面的能力校验和仓库装配，不会立刻进入 workflow.run。
            # build 返回可复用的算法对象，此时还没有用户问题，不执行 retrieve/project。
            # 转置P4的组件，返回P4Components对象
            graph_gateway=graph_gateway,
            language_model=language_model,
        )
        # 定义 3 个“保留能力名”。
        reserved_capabilities = {"session_repository", "legal_graph", "llm"}
        # 获取P4组件的能力名列表
        component_names = [item.capability for item in components.capabilities]
        # set() 会去重。
        if len(component_names) != len(set(component_names)):
            raise ValueError("P4 capability names must be unique")
        if reserved_capabilities & set(component_names):
            raise ValueError("P4 capabilities cannot replace P1 infrastructure capabilities")

        if repository is None:
            # 【默认 Live 也在用】内存仓库不是 Demo 专用；当前保存跨轮会话，重启会丢失。
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
            # 【启动 5】跳到 workflow/engine.py 的 CasePathWorkflow.__init__。
            # 构造函数仅保存六个算法依赖及追问预算，随后返回当前函数继续执行。
            # 单轮执行器只负责顺序；各端口上的具体算法来自 components。
            components.workflow_dependencies,
            max_question_turns=settings.max_question_turns,
        )
        service = build_session_service(
            # 【启动 6】跳到本文件末尾 build_session_service，再到
            # application/session_service.py 的 SessionService.__init__。
            # service 保存 repository、workflow、answer_interpreter 三个对象。
            # 会话服务包住单轮执行器，增加回答回执、来源校验及并发版本保护。
            workflow=workflow,
            repository=repository,
            answer_interpreter=components.answer_interpreter,
        )
        runtime = LiveRuntime(
            # 【启动 7】把装配好的 service 和客户端打包。LiveRuntime 是运行容器，
            # 和每轮结果 WorkflowSnapshot 不同，此处也没有创建用户会话。
            service=service,
            repository_capability=repository_capability,
            p4_capabilities=components.capabilities,
            graph_gateway=graph_gateway,
            language_model=language_model,
            graph_initialization_failed=graph_failed,
            llm_initialization_failed=llm_failed,
        )
        #  清空 ExitStack 里登记的关闭回调
        cleanup.pop_all()
        # 将清理责任转交 runtime.close，避免离开 with 时就把正常客户端关闭。
        # 【启动 8】return 后回到 api/main.py:create_live_app 的 runtime = ... 下一行，
        # 接着执行 create_app(runtime.service, ...) 注册 HTTP 路由。
        return runtime


def build_session_service(
    *,
    workflow: CasePathWorkflow,
    repository: SessionRepository,
    answer_interpreter: AnswerInterpreter | None,
) -> SessionService:
    """注入正式或测试组件，不在此创建Neo4j/LLM凭据或偷偷回退Demo。"""
    return SessionService(
        # 只调用 SessionService.__init__，不是 create_session；此时尚无用户 query。
        repository=repository,
        workflow=workflow,
        answer_interpreter=answer_interpreter,
    )
