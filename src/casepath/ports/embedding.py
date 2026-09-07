"""P1 embedding 端口：与生成式 LLM 分离，模型版本与维度必须显式给定。"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EmbeddingSpec:
    model: str
    revision: str
    dimensions: int

    # 是 dataclass 的特殊方法。赋值后自动调用检查是否合法
    def __post_init__(self):
        if not self.model.strip() or not self.revision.strip() or self.dimensions <= 0:
            raise ValueError("embedding 模型、版本和维度必须有效")


# 检查模板，当后续函数调用时，必须规定提供spec类和embed函数
class EmbeddingModel(Protocol):
    spec: EmbeddingSpec

    def embed(self, texts: list[str]) -> list[list[float]]: ...
