"""P1 embedding 独立配置。没有向量索引路径时不启用，不复用聊天模型 Key。"""

import os
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr

from casepath.adapters.embedding_client import EmbeddingClient
from casepath.ports.embedding import EmbeddingSpec


class EmbeddingSettings(BaseModel):
    api_key: SecretStr
    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    dimensions: int = Field(gt=0)
    timeout_seconds: float = Field(default=30, gt=0)
    batch_size: int = Field(default=32, gt=0)
    index_path: Path

    @classmethod
    def from_env(cls):
        return cls(
            api_key=os.environ["CASEPATH_EMBEDDING_API_KEY"],
            base_url=os.environ["CASEPATH_EMBEDDING_BASE_URL"],
            model=os.environ["CASEPATH_EMBEDDING_MODEL"],
            revision=os.environ["CASEPATH_EMBEDDING_REVISION"],
            dimensions=os.environ["CASEPATH_EMBEDDING_DIMENSIONS"],
            timeout_seconds=os.getenv("CASEPATH_EMBEDDING_TIMEOUT_SECONDS", "30"),
            batch_size=os.getenv("CASEPATH_EMBEDDING_BATCH_SIZE", "32"),
            index_path=os.environ["CASEPATH_VECTOR_INDEX"],
        )

    def connect(self):
        from openai import OpenAI

        # 显式关闭 SDK 自动重试，避免一次查询不可控地放大延迟和费用。
        client = OpenAI(
            api_key=self.api_key.get_secret_value(),
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            max_retries=0,
        )
        return EmbeddingClient(
            client,
            EmbeddingSpec(self.model, self.revision, self.dimensions),
            timeout_seconds=self.timeout_seconds,
            batch_size=self.batch_size,
        )
