"""适配器按职责分组。

- infrastructure：外部连接与会话存储
- retrieval：规则与案例检索
- conditions：条件映射与比较
- questioning：追问选择
- explanation：证据组织与解释
- demo：离线演示/测试脚手架
- p4_factory：正式算法装配入口
"""

from casepath.adapters.infrastructure.neo4j_gateway import Neo4jGraphGateway
from casepath.adapters.infrastructure.structured_llm import (
    OpenAICompatibleStructuredLanguageModel,
)

__all__ = [
    "Neo4jGraphGateway",
    "OpenAICompatibleStructuredLanguageModel",
]
