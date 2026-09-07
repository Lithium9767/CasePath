"""P1 配置化启动入口：环境配置 → 基础设施客户端 → P4 工厂 → HTTP。

使用 uvicorn 的 --factory 调用；不在模块导入时连接模型或数据库。
旧的 casepath.api.main:app 仍是 Demo，本入口才读取 CASEPATH_MODE。
"""

import os
from contextlib import asynccontextmanager

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

    embedding = None
    index = None
    try:
        if os.getenv("CASEPATH_VECTOR_INDEX"):
            from casepath.adapters.vector_search import VectorIndex
            from casepath.config.embedding import EmbeddingSettings

            embedding_settings = EmbeddingSettings.from_env()
            index = VectorIndex.load(embedding_settings.index_path)
            embedding = embedding_settings.connect()
        application = create_live_app(
            HybridCypherP4Factory(
                data_root=settings.data_root, embedding_model=embedding, vector_index=index
            ),
            settings=settings,
        )
    except Exception:
        if embedding is not None:
            embedding.close()
        raise
    if embedding is not None:
        original_lifespan = application.router.lifespan_context

        @asynccontextmanager
        async def lifespan(app):
            try:
                async with original_lifespan(app):
                    yield
            finally:
                embedding.close()

        application.router.lifespan_context = lifespan
    return application
