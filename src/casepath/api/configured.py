"""P1 配置化启动入口：环境配置 → 基础设施客户端 → P4 工厂 → HTTP。

使用 uvicorn 的 --factory 调用；不在模块导入时连接模型或数据库。
旧的 casepath.api.main:app 仍是 Demo，本入口才读取 CASEPATH_MODE。
"""

from fastapi import FastAPI

from casepath.api.main import create_app, create_live_app
from casepath.config import RuntimeMode, RuntimeSettings


def create_configured_app() -> FastAPI:
    """uv 负责 --env-file 加载；这里只读进程环境，不搜索或覆盖用户的 .env。"""
    settings = RuntimeSettings.from_env()
    if settings.mode is RuntimeMode.DEMO:
        return create_app()

    # 只在明确选择 Live 后加载正式算法；缺失数据或装配失败直接报错。
    from casepath.adapters.p4_factory import HybridCypherP4Factory

    return create_live_app(
        HybridCypherP4Factory(data_root=settings.data_root),
        settings=settings,
    )
