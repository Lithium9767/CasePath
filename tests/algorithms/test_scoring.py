from casepath.algorithms.scoring import (
    calculate_case_rerank_score,
    normalize_positive_scores,
)


def test_normalize_positive_scores_by_maximum():
    """正分数应按候选集合最大值缩放到0到1。"""

    assert normalize_positive_scores([2.0, 1.0, 0.0]) == [1.0, 0.5, 0.0]


def test_normalize_empty_or_all_zero_scores():
    """空集合或全零集合不应产生除零错误。"""

    assert normalize_positive_scores([]) == []
    assert normalize_positive_scores([0.0, 0.0]) == [0.0, 0.0]


def test_negative_scores_are_clamped_to_zero():
    """归一化函数只接受正向相关性，负数按零处理。"""

    assert normalize_positive_scores([-2.0, 1.0]) == [0.0, 1.0]


def test_case_rerank_score_uses_planned_weights():
    """案例重排必须严格使用开发计划冻结的四项权重。"""

    score, components = calculate_case_rerank_score(
        bm25_score=0.8,
        condition_overlap=0.6,
        rule_overlap=0.5,
        source_quality=1.0,
    )

    assert score == 0.45 * 0.8 + 0.25 * 0.6 + 0.20 * 0.5 + 0.10 * 1.0
    assert [component.name for component in components] == [
        "bm25",
        "condition_overlap",
        "rule_overlap",
        "source_quality",
    ]


def test_case_rerank_components_are_clamped_to_unit_interval():
    """异常的上游特征不能让最终重排分数超出0到1。"""

    score, components = calculate_case_rerank_score(
        bm25_score=2.0,
        condition_overlap=-1.0,
        rule_overlap=5.0,
        source_quality=3.0,
    )

    assert score == 0.75
    assert all(0.0 <= component.value <= 1.0 for component in components)
