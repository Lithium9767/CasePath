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
    service: SessionService | None = None,
    capabilities: list[CapabilityStatus] | None = None,
    *,
    capability_provider: Callable[[], list[CapabilityStatus]] | None = None,
    shutdown_callbacks: Sequence[Callable[[], None]] = (),
) -> FastAPI:
    """默认显式装配 Demo；注入真实组件时应同时提供其能力清单。"""
    if capabilities is not None and capability_provider is not None:
        raise ValueError("provide capabilities or capability_provider, not both")

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            yield
        finally:
            for callback in reversed(shutdown_callbacks):
                callback()

    application = FastAPI(title="CasePath API", version="0.1.0", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if service is None:
        service = build_demo_session_service()
        if capabilities is None and capability_provider is None:
            capabilities = build_demo_capabilities()
    application.state.session_service = service
    static_capabilities = tuple(item.model_copy(deep=True) for item in capabilities or [])
    # 不猜测外部注入组件是否为LIVE；没有清单或提供器时返回空列表。
    application.state.capability_provider = capability_provider or (
        lambda: [item.model_copy(deep=True) for item in static_capabilities]
    )
    register_error_handlers(application)
    application.include_router(router)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": "0.1.0"}

    @application.get("/v1/capabilities", response_model=list[CapabilityStatus])
    def get_capabilities() -> list[CapabilityStatus]:
        return application.state.capability_provider()

    @application.get("/v1/contracts/{contract_name}/schema")
    def contract_schema(contract_name: str) -> dict:
        model = CONTRACTS.get(contract_name)
        if model is None:
            raise HTTPException(status_code=404)
        return model.model_json_schema()

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
        settings or RuntimeSettings.from_env(),
        p4_factory,
        repository=repository,
        repository_capability=repository_capability,
    )
    application = create_app(
        runtime.service,
        capability_provider=runtime.capabilities,
        shutdown_callbacks=(runtime.close,),
    )
    application.state.live_runtime = runtime
    return application


app = create_app()
