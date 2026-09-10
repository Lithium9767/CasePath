"""P1 配置化启动入口：环境配置 → 基础设施客户端 → P4 工厂 → HTTP。

使用 uvicorn 的 --factory 调用；不在模块导入时连接模型或数据库。
旧的 casepath.api.main:app 仍是 Demo，本入口才读取 CASEPATH_MODE。
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from casepath.api.main import create_app, create_live_app
from casepath.config import RuntimeMode, RuntimeSettings


# 正式启动
# uv run --env-file .env uvicorn casepath.api.configured:create_configured_app --factory --host 127.0.0.1 --port 8000
def create_configured_app() -> FastAPI:
    # 【阅读入口】启动时调用一次：配置 → 客户端/索引 → P4 工厂 → FastAPI。
    # 用户每次提问不会重新进入这里，而是进入 api/sessions.py 的会话路由。
    """uv 负责 --env-file 加载；这里只读进程环境，不搜索或覆盖用户的 .env。"""
    # uv把.env文件中的环境变量加载到环境变量中
    settings = RuntimeSettings.from_env()
    # 如果settings.mode是DEMO，则返回create_app()
    if settings.mode is RuntimeMode.DEMO:
        # 【仅 Demo 分支】Live 不执行此分支；保留它供无外部服务时演示和测试。
        return create_app()

    # 只在明确选择 Live 后加载正式算法；缺失数据或装配失败直接报错。
    from casepath.adapters.p4_factory import HybridCypherP4Factory

    # 假设模型没有向量能力
    embedding = None
    index = None
    # 尝试加载向量索引和嵌入模型
    try:
        if os.getenv("CASEPATH_VECTOR_INDEX"):
            # 【可选 Live 能力】有索引配置才启用。这里加载已建索引，不做全库 embedding。
            from casepath.adapters.retrieval.vector_search import VectorIndex
            from casepath.config.embedding import EmbeddingSettings

            # 从环境变量中加载嵌入设置
            embedding_settings = EmbeddingSettings.from_env()
            # 加载已经创建好的向量索引
            index = VectorIndex.load(embedding_settings.index_path)
            # 创建客户端
            embedding = embedding_settings.connect()
        # 【当前入口未启用】未传 calibrator，工厂默认使用 None，不代表已校准置信度。
        # 手工构造工厂仍可注入校准器；需要先用独立标注样本拟合，不能直接删除该扩展点。
        application = create_live_app(
            # 【跨文件跳转 1】create_live_app 来自 api/main.py，并非 bootstrap.py。
            # Python 先执行下面的 HybridCypherP4Factory(...) 构造参数对象，再进入该函数。
            # 工厂 __init__ 仅保存资源；真正 build 算法组件要等 bootstrap 调用。
            # 下一站：api/main.py → create_live_app() → build_live_runtime(...)。
            HybridCypherP4Factory(
                data_root=settings.data_root, embedding_model=embedding, vector_index=index
            ),
            settings=settings,
        )
    except Exception:
        # 如果加载失败即try中抛出了异常，则先关闭嵌入模型，以防止资源泄露
        if embedding is not None:
            embedding.close()
        # 重新抛出异常，让上层捕获并处理
        raise

    # 包裹lifespan，在embedding结束时关闭嵌入模型，以防止资源泄露
    if embedding is not None:
        # 负责“原来 FastAPI 已经有的启动/关闭逻辑”
        original_lifespan = application.router.lifespan_context

        # 异步函数，内部可以异步程序语言
        # with语法糖，保证资源打开后关闭
        @asynccontextmanager
        async def lifespan(app):
            try:
                async with original_lifespan(app):
                    # 正常运行，先暂停在这里，以后还会回来继续执行。
                    yield
            finally:
                # 关闭防止泄露
                embedding.close()

        # 覆盖原来FastAPI的lifespan_context，使用我们自己的lifespan
        application.router.lifespan_context = lifespan
    # 【启动完成】应用返回 uvicorn，接下来等待 HTTP 请求；不是继续自动执行 A-E。
    # 首次问题：api/sessions.py:create_session → SessionService.create_session → _run。
    # 补充回答：api/sessions.py:submit_answer → SessionService.submit_answer → _run。
    # 两条请求链最终都进入 workflow/engine.py:CasePathWorkflow.run。
    return application
