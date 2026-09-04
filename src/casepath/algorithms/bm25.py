from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchDocument:
    """BM25索引使用的不可变文档。

    `object_id`用于在命中后回查RuleRecord或CaseRecord；`tokens`由上游
    分词器提前生成，使BM25实现只负责统计和评分，不耦合具体语言。
    """

    object_id: str
    tokens: tuple[str, ...]

    def __post_init__(self) -> None:
        """拒绝无法稳定回查原始实体的空文档ID。"""

        if not self.object_id.strip():
            raise ValueError("文档ID不能为空")


class BM25Index:
    """确定性的内存BM25索引。

    索引在初始化时一次性计算词频、文档频率和逆文档频率。查询过程只读，
    因而相同语料、参数和查询会得到相同结果，适合CasePath第一阶段的离线
    评测。该实现采用常见的Okapi BM25形式，不考虑查询词频。
    """

    def __init__(
        self,
        documents: list[SearchDocument],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        """构建索引并预计算后续查询需要的统计量。

        Args:
            documents: 已完成分词的检索文档。
            k1: 控制词频饱和速度，必须是有限正数。
            b: 控制文档长度归一化程度，取值范围为0到1。

        Raises:
            ValueError: 参数非法或出现重复文档ID时抛出。
        """

        self._validate_parameters(k1=k1, b=b)
        self._validate_unique_ids(documents)

        self._documents = tuple(documents)
        self._k1 = k1
        self._b = b

        # 每篇文档单独保存词频，查询时无需再次遍历全部词元。
        self._term_frequencies = {
            document.object_id: Counter(document.tokens) for document in self._documents
        }
        self._document_lengths = {
            document.object_id: len(document.tokens) for document in self._documents
        }

        document_count = len(self._documents)
        total_length = sum(self._document_lengths.values())
        self._average_document_length = (
            total_length / document_count if document_count > 0 else 0.0
        )

        # 文档频率按“包含该词元的文档数”统计，同一文档中的重复词只计一次。
        document_frequencies: Counter[str] = Counter()
        for document in self._documents:
            document_frequencies.update(set(document.tokens))

        # 使用带平滑且恒为正的IDF形式，避免高频词产生负分。
        self._inverse_document_frequencies = {
            token: math.log1p(
                (document_count - frequency + 0.5) / (frequency + 0.5)
            )
            for token, frequency in document_frequencies.items()
        }

    def search(
        self,
        query_tokens: list[str],
        *,
        top_k: int,
    ) -> list[tuple[str, float]]:
        """返回按BM25得分降序排列的文档ID和分数。

        完全没有命中的零分文档不会返回。同分时使用对象ID升序作为稳定的
        次级排序键，避免语料输入顺序或运行环境影响Top-K结果。
        """

        if not self._documents or not query_tokens or top_k <= 0:
            return []

        # 第一版不计算查询词频。去重还可以避免用户重复输入词语时人为放大分数。
        unique_query_tokens = tuple(
            dict.fromkeys(token for token in query_tokens if token)
        )
        if not unique_query_tokens:
            return []

        scored_documents: list[tuple[str, float]] = []
        for document in self._documents:
            score = self._score_document(
                object_id=document.object_id,
                query_tokens=unique_query_tokens,
            )
            if score > 0.0:
                scored_documents.append((document.object_id, score))

        scored_documents.sort(key=lambda item: (-item[1], item[0]))
        return scored_documents[:top_k]

    def _score_document(
        self,
        *,
        object_id: str,
        query_tokens: tuple[str, ...],
    ) -> float:
        """计算一篇文档相对于当前查询的BM25分数。"""

        term_frequencies = self._term_frequencies[object_id]
        document_length = self._document_lengths[object_id]

        # 平均长度为0意味着索引中所有文档都没有词元，此时不可能产生有效命中。
        if self._average_document_length == 0.0:
            return 0.0

        length_ratio = document_length / self._average_document_length
        length_normalization = self._k1 * (
            1.0 - self._b + self._b * length_ratio
        )

        score = 0.0
        for token in query_tokens:
            term_frequency = term_frequencies.get(token, 0)
            inverse_document_frequency = self._inverse_document_frequencies.get(token)

            # 查询词不在当前文档或整个语料中时，对该文档没有贡献。
            if term_frequency == 0 or inverse_document_frequency is None:
                continue

            numerator = term_frequency * (self._k1 + 1.0)
            denominator = term_frequency + length_normalization
            score += inverse_document_frequency * numerator / denominator

        return score

    @staticmethod
    def _validate_parameters(*, k1: float, b: float) -> None:
        """校验参数范围，防止NaN或无穷值污染排序。"""

        if not math.isfinite(k1) or k1 <= 0.0:
            raise ValueError("BM25参数k1必须是有限正数")
        if not math.isfinite(b) or not 0.0 <= b <= 1.0:
            raise ValueError("BM25参数b必须是0到1之间的有限数")

    @staticmethod
    def _validate_unique_ids(documents: list[SearchDocument]) -> None:
        """确保每个检索结果都能唯一映射回原始合同对象。"""

        seen_ids: set[str] = set()
        for document in documents:
            if document.object_id in seen_ids:
                raise ValueError(f"文档ID不能重复：{document.object_id}")
            seen_ids.add(document.object_id)
