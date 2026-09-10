"""正式应用工厂；应用状态在不同实例之间互相隔离。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from casepath.adapters.demo import build_demo_capabilities, build_demo_session_service
from casepath.application.session_service import SessionService
from casepath.bootstrap import (
    P4ComponentFactory,
    build_live_runtime,
)
from casepath.config import RuntimeSettings
from casepath.contracts import CapabilityStatus
from casepath.contracts.registry import CONTRACTS
from casepath.ports.session_repository import SessionRepository

from .errors import register_error_handlers
from .sessions import router


def create_app(
    # service 是已装配的会话服务；正式入口传 runtime.service，省略则构造 Demo 服务。
    service: SessionService | None = None,
    # capabilities 是固定能力清单，适合 Demo/测试；与下面的动态提供函数只能选一种。
    capabilities: list[CapabilityStatus] | None = None,
    *,
    # Callable[[], ...] 表示无参数函数；传 runtime.capabilities，不加括号，稍后按请求调用。
    capability_provider: Callable[[], list[CapabilityStatus]] | None = None,
    # 关闭回调也是函数对象列表。正式入口传 (runtime.close,)，逗号表示单元素元组。
    shutdown_callbacks: Sequence[Callable[[], None]] = (),
) -> FastAPI:
    """默认显式装配 Demo；注入真实组件时应同时提供其能力清单。"""
    # 【本函数的职责】把已装配的服务包装成 HTTP 应用，不在这里实现法律检索算法。
    # 执行顺序：参数检查 → 定义生命周期 → 创建应用/CORS → 挂载服务与能力 → 注册接口 → 返回。
    # create_app 返回的是 ASGI 应用对象；绑定 8000 端口并接收请求的是外面的 uvicorn。
    if capabilities is not None and capability_provider is not None:
        raise ValueError("provide capabilities or capability_provider, not both")

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        # 定义这个函数时不会运行函数体；服务器进入应用生命周期时才执行到 yield。
        # yield 把控制权交回服务器，应用开始处理请求；关闭时继续执行 finally。
        # 它不是每处理一个请求就执行一次，也不在这里重新创建 Neo4j/LLM 客户端。
        try:
            yield
        finally:
            # 逆序执行关闭回调，与资源通常按相反顺序释放的约定一致。
            # 当前循环没有逐项捕获异常：若某个 callback 抛错，后续回调不会自动继续。
            for callback in reversed(shutdown_callbacks):
                callback()

    application = FastAPI(title="CasePath API", version="0.1.0", lifespan=lifespan)
    # title/version 是 API 文档元信息；version 不是 RuleRecord 等公共合同的版本。
    # lifespan=lifespan 传入函数本身，交给框架管理；默认还会提供 /docs 等文档入口。
    application.add_middleware(
        CORSMiddleware,
        # CORS 处理浏览器跨源访问：前端 5173 与后端 8000 的端口不同，属于不同源。
        # 只允许下面两个开发前端来源；它不是登录鉴权，也不是阻止非浏览器请求的安全机制。
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_credentials=True,
        # 允许浏览器携带凭据，但并不等于前端实际发送凭据，更不等于已经实现用户登录。
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if service is None:
        # 这是“未注入服务”分支，不是“Live 调用失败后自动回退 Demo”的分支。
        service = build_demo_session_service()
        if capabilities is None and capability_provider is None:
            capabilities = build_demo_capabilities()
    application.state.session_service = service
    # 【启动 9】正式入口传入的是 runtime.service，因此上面的 service is None 不成立。
    # 将同一个服务对象挂到应用上；api/sessions.py:get_session_service 从这里取回它。
    static_capabilities = tuple(item.model_copy(deep=True) for item in capabilities or [])
    # 先深拷贝、存成元组，避免外部修改传入清单影响应用；返回时还会再复制每个对象。
    # 元组只限制容器成员替换，不能让里面的 Pydantic 对象自动变成不可变对象。
    # 不猜测外部注入组件是否为LIVE；没有清单或提供器时返回空列表。
    application.state.capability_provider = capability_provider or (
        # Live 保存传入的动态函数；没有传入时保存下面的 lambda，而不是马上执行查询。
        # state 是本应用共享状态，不是某个用户的 QueryState，也不是持久化数据库。
        lambda: [item.model_copy(deep=True) for item in static_capabilities]
    )
    register_error_handlers(application)
    # 下一站 api/errors.py：注册异常到 HTTP 响应的映射，比如会话冲突 409、模型不可用 503。
    # 此刻只登记处理器；请求执行时抛出对应异常，框架才调用它，不泄露内部堆栈或密钥。
    application.include_router(router)
    # 【启动 10】router 来自 api/sessions.py，注册 /v1/sessions 等 URL。
    # 注册不等于执行路由函数；启动结束后，收到匹配的 HTTP 请求才调用它们。

    @application.get("/health")
    def health() -> dict[str, str]:
        # 装饰器在 create_app 执行时登记 URL，函数体在 GET /health 到来时才执行。
        # 这里只证明 HTTP 应用能响应，不探测图数据库、不调用模型，也不证明全链路可用。
        return {"status": "ok", "version": "0.1.0"}

    @application.get("/v1/capabilities", response_model=list[CapabilityStatus])
    def get_capabilities() -> list[CapabilityStatus]:
        # 到这里加 () 才执行已保存的提供函数。Live 对应 bootstrap.LiveRuntime.capabilities。
        # response_model 让 FastAPI 按合同验证并序列化返回列表；不是在这里重新装配能力。
        return application.state.capability_provider()

    @application.get("/v1/contracts/{contract_name}/schema")
    def contract_schema(contract_name: str) -> dict:
        # URL 中的 {contract_name} 由框架传给同名参数，例如请求某个合同的结构说明。
        # CONTRACTS 是名称到 Pydantic 类的注册表；这里导出 Schema，不返回业务数据样本。
        model = CONTRACTS.get(contract_name)
        if model is None:
            # 未注册名称返回 404，随后由已注册的 HTTP 异常处理器组织统一错误响应。
            raise HTTPException(status_code=404)
        return model.model_json_schema()

    # 【回到调用方】Live 回到下方 create_live_app，再回 configured.py，最后交给 uvicorn。
    # 真正问答入口已通过 include_router 注册，继续阅读 api/sessions.py 的三个路由。
    return application


def create_live_app(
    p4_factory: P4ComponentFactory,
    *,
    settings: RuntimeSettings | None = None,
    repository: SessionRepository | None = None,
    repository_capability: CapabilityStatus | None = None,
) -> FastAPI:
    """正式应用入口；P4工厂必须显式提供，绝不自动替换成Demo算法。"""

    runtime = build_live_runtime(
        # 【跨文件跳转 2】configured.py 的 create_live_app(...) 进入当前函数后走到这里。
        # build_live_runtime 从 casepath.bootstrap 导入，下一站是 bootstrap.py 同名函数。
        # 将配置和 P4 工厂传过去，取得包含会话服务、客户端及能力查询方法的运行时对象。
        settings or RuntimeSettings.from_env(),
        p4_factory,
        repository=repository,
        repository_capability=repository_capability,
    )
    application = create_app(
        # 【返回路径】bootstrap 装配完成后回到这里，把 runtime.service 挂到 HTTP 应用。
        # 再返回 configured.py，由它补充 embedding 的关闭逻辑并将应用交给 uvicorn。
        runtime.service,
        capability_provider=runtime.capabilities,
        shutdown_callbacks=(runtime.close,),
    )
    application.state.live_runtime = runtime
    # 【启动 11】返回 configured.py 的 application = create_live_app(...) 调用点。
    # 后面 configured 补充 embedding 关闭逻辑，再将应用返回 uvicorn。
    return application


# 下面的模块级 app 是独立 Demo 应用；导入 main.py 时会创建它，
# 但 configured 的 Live 启动最终返回另一个已注入 runtime.service 的应用。
app = create_app()
