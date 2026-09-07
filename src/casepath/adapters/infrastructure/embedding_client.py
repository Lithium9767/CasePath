"""P1 embedding 传输适配器：注入 SDK 客户端，不复用聊天完成接口。"""

import math

from casepath.application.errors import RetrieverUnavailable
from casepath.ports.embedding import EmbeddingSpec


class EmbeddingClient:
    def __init__(self, client, spec: EmbeddingSpec, *, timeout_seconds=30, batch_size=32):
        if timeout_seconds <= 0 or batch_size <= 0:
            raise ValueError("embedding 超时和批次大小必须为正")
        self.client, self.spec = client, spec
        self.timeout_seconds, self.batch_size = timeout_seconds, batch_size

    def embed(self, texts):
        output = []
        try:
            for start in range(0, len(texts), self.batch_size):
                chunk = texts[start : start + self.batch_size]
                if any(not text.strip() for text in chunk):
                    raise ValueError("不接受空文本")
                response = self.client.embeddings.create(
                    input=chunk,
                    model=self.spec.model,
                    timeout=self.timeout_seconds,
                )
                records = sorted(response.data, key=lambda item: item.index)
                if [item.index for item in records] != list(range(len(chunk))):
                    raise ValueError("embedding 返回索引不完整")
                for item in records:
                    if len(item.embedding) != self.spec.dimensions or any(
                        not math.isfinite(value) for value in item.embedding
                    ):
                        raise ValueError("embedding 维度或数值无效")
                output.extend([list(item.embedding) for item in records])
        except Exception as exc:
            raise RetrieverUnavailable("embedding 服务调用失败") from exc
        return output

    def close(self):
        self.client.close()
