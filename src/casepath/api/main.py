"""正式应用工厂；应用状态在不同实例之间互相隔离。"""

from fastapi import FastAPI, HTTPException

from casepath.application.session_service import SessionService
from casepath.bootstrap import build_demo_capabilities, build_demo_session_service
from casepath.contracts import CapabilityStatus
from casepath.contracts.registry import CONTRACTS

from .errors import register_error_handlers
from .sessions import router


def create_app(
    service: SessionService | None = None,
    capabilities: list[CapabilityStatus] | None = None,
) -> FastAPI:
    """默认显式装配 Demo；注入真实组件时应同时提供其能力清单。"""
    application = FastAPI(title="CasePath API", version="0.1.0")
    if service is None:
        service = build_demo_session_service()
        if capabilities is None:
            capabilities = build_demo_capabilities()
    application.state.session_service = service
    # 不猜测外部注入组件是否为 LIVE；没有清单时返回空列表。
    application.state.capabilities = [item.model_copy(deep=True) for item in capabilities or []]
    register_error_handlers(application)
    application.include_router(router)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": "0.1.0"}

    @application.get("/v1/capabilities", response_model=list[CapabilityStatus])
    def get_capabilities() -> list[CapabilityStatus]:
        return application.state.capabilities

    @application.get("/v1/contracts/{contract_name}/schema")
    def contract_schema(contract_name: str) -> dict:
        model = CONTRACTS.get(contract_name)
        if model is None:
            raise HTTPException(status_code=404)
        return model.model_json_schema()

    return application


app = create_app()
