from __future__ import annotations

from casepath.contracts import ScoreComponent


def _clamp_unit_interval(value: float) -> float:
    """将上游特征限制在0到1，防止异常值破坏最终排序。"""

    return max(0.0, min(1.0, value))


def normalize_positive_scores(scores: list[float]) -> list[float]:
    """使用候选集合中的最大值归一化非负相关性分数。

    BM25只产生正向相关性，因此负数统一视为0。采用最大值缩放而不是
    Min-Max，可以在所有命中文档同分时保留BM25贡献：同分文档都会得到1，
    再由条件、规则和来源质量进行区分。
    """

    if not scores:
        return []

    nonnegative_scores = [max(0.0, score) for score in scores]
    maximum = max(nonnegative_scores)
    if maximum == 0.0:
        return [0.0 for _ in nonnegative_scores]

    return [score / maximum for score in nonnegative_scores]


def calculate_case_rerank_score(
    *,
    bm25_score: float,
    condition_overlap: float,
    rule_overlap: float,
    source_quality: float,
) -> tuple[float, list[ScoreComponent]]:
    """按照开发计划中的固定权重计算案例重排分数。

    公式为：0.45B + 0.25C + 0.20R + 0.10S。当前权重是可解释的
    原型启发式参数，不代表经过训练或实验得到的最优参数。
    """

    normalized_bm25 = _clamp_unit_interval(bm25_score)
    normalized_condition = _clamp_unit_interval(condition_overlap)
    normalized_rule = _clamp_unit_interval(rule_overlap)
    normalized_source = _clamp_unit_interval(source_quality)

    score = (
        0.45 * normalized_bm25
        + 0.25 * normalized_condition
        + 0.20 * normalized_rule
        + 0.10 * normalized_source
    )
    components = [
        ScoreComponent(
            name="bm25",
            value=normalized_bm25,
            explanation="BM25初始召回分数在当前候选集合中的归一化结果。",
        ),
        ScoreComponent(
            name="condition_overlap",
            value=normalized_condition,
            explanation="用户已知条件与案例条件认定的一致程度。",
        ),
        ScoreComponent(
            name="rule_overlap",
            value=normalized_rule,
            explanation="案例引用规则与当前候选规则的重合程度。",
        ),
        ScoreComponent(
            name="source_quality",
            value=normalized_source,
            explanation="案例成熟度和可追溯原文的综合质量。",
        ),
    ]
    return score, components
