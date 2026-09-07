"""P4 校准器：只用独立标注集的映射正确性标签拟合，不使用法律结果标签。"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ConfidenceCalibrator:
    dataset_id: str
    # 单调分段校准表：(分段最大原始分数，校准正确率)。
    bins: tuple[tuple[float, float], ...]

    @classmethod
    def fit(cls, samples: list[tuple[float, bool]], *, dataset_id: str):
        if not samples or not dataset_id.strip():
            raise ValueError("必须提供独立校准集和版本 ID")
        grouped = {}
        for score, correct in samples:
            if not math.isfinite(score) or not 0 <= score <= 1 or not isinstance(correct, bool):
                raise ValueError("校准样本必须为有限分数和布尔正确性标签")
            hits, count = grouped.get(score, (0, 0))
            grouped[score] = hits + int(correct), count + 1
        blocks = []
        for score, (hits, count) in sorted(grouped.items()):
            blocks.append([score, hits + 1, count + 2])
            # 加一平滑后做单调回归，避免小样本给出 0/1 的过度确信。
            while len(blocks) > 1 and blocks[-2][1] / blocks[-2][2] > blocks[-1][1] / blocks[-1][2]:
                right, left = blocks.pop(), blocks.pop()
                blocks.append([right[0], left[1] + right[1], left[2] + right[2]])
        return cls(dataset_id, tuple((score, hits / count) for score, hits, count in blocks))

    def transform(self, score):
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("原始置信度无效")
        return next((value for upper, value in self.bins if score <= upper), self.bins[-1][1])
