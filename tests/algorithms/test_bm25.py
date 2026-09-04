import math

import pytest

from casepath.algorithms.bm25 import BM25Index, SearchDocument


def make_document(object_id: str, *tokens: str) -> SearchDocument:
    """创建测试文档，减少各测试重复的数据构造代码。"""

    return SearchDocument(object_id=object_id, tokens=tuple(tokens))


def test_more_relevant_document_ranks_first():
    """包含更多查询词元的文档应获得更高排名。"""

    index = BM25Index(
        [
            make_document("rule.refund", "合同", "解除", "返还", "余额"),
            make_document("rule.lease", "合同", "租赁", "房屋"),
            make_document("rule.tort", "侵权", "损害", "赔偿"),
        ]
    )

    results = index.search(["合同", "解除", "余额"], top_k=3)

    assert [object_id for object_id, _ in results] == ["rule.refund", "rule.lease"]
    assert results[0][1] > results[1][1] > 0


def test_search_is_deterministic():
    """相同语料和查询必须始终产生完全一致的结果。"""

    index = BM25Index(
        [
            make_document("case.b", "健身", "停业", "退款"),
            make_document("case.a", "健身", "闭店", "余额"),
        ]
    )

    first = index.search(["健身", "退款"], top_k=10)
    second = index.search(["健身", "退款"], top_k=10)

    assert first == second


def test_equal_scores_are_ordered_by_object_id():
    """同分文档应按对象ID升序排列，避免输入顺序影响结果。"""

    index = BM25Index(
        [
            make_document("case.b", "合同", "解除"),
            make_document("case.a", "合同", "解除"),
        ]
    )

    results = index.search(["合同"], top_k=2)

    assert [object_id for object_id, _ in results] == ["case.a", "case.b"]


def test_top_k_limits_number_of_results():
    """检索结果数量不能超过调用方指定的Top-K。"""

    index = BM25Index(
        [
            make_document("doc.1", "合同"),
            make_document("doc.2", "合同"),
            make_document("doc.3", "合同"),
        ]
    )

    assert len(index.search(["合同"], top_k=2)) == 2


def test_empty_inputs_return_empty_result():
    """空语料、空查询和非正数Top-K都应安全返回空列表。"""

    empty_index = BM25Index([])
    populated_index = BM25Index([make_document("doc.1", "合同")])

    assert empty_index.search(["合同"], top_k=5) == []
    assert populated_index.search([], top_k=5) == []
    assert populated_index.search(["合同"], top_k=0) == []
    assert populated_index.search(["合同"], top_k=-1) == []


def test_unknown_query_terms_return_empty_result():
    """查询词元完全不在语料中时，不应返回一批零分文档。"""

    index = BM25Index([make_document("doc.1", "合同", "解除")])

    assert index.search(["侵权"], top_k=5) == []


def test_repeated_query_tokens_do_not_multiply_score():
    """第一版BM25不计算查询词频，重复输入同一词元不应提高分数。"""

    index = BM25Index([make_document("doc.1", "合同", "解除")])

    single = index.search(["合同"], top_k=1)
    repeated = index.search(["合同", "合同", "合同"], top_k=1)

    assert single == repeated


def test_shorter_document_gets_higher_score_for_same_term_frequency():
    """词频相同时，长度归一化应使更短且更聚焦的文档得分更高。"""

    index = BM25Index(
        [
            make_document("doc.short", "解除", "合同"),
            make_document("doc.long", "解除", "合同", "服务", "付款", "余额", "通知"),
        ]
    )

    results = index.search(["解除"], top_k=2)

    assert [object_id for object_id, _ in results] == ["doc.short", "doc.long"]


def test_scores_are_finite_and_positive():
    """正常命中的BM25分数必须是有限正数。"""

    index = BM25Index([make_document("doc.1", "合同", "合同", "解除")])

    _, score = index.search(["合同"], top_k=1)[0]

    assert math.isfinite(score)
    assert score > 0


def test_duplicate_document_ids_are_rejected():
    """重复ID会导致检索结果无法回查实体，构建索引时必须拒绝。"""

    with pytest.raises(ValueError, match="文档ID不能重复"):
        BM25Index(
            [
                make_document("doc.same", "合同"),
                make_document("doc.same", "解除"),
            ]
        )


@pytest.mark.parametrize(
    ("k1", "b"),
    [
        (0.0, 0.75),
        (-1.0, 0.75),
        (1.5, -0.01),
        (1.5, 1.01),
    ],
)
def test_invalid_parameters_are_rejected(k1: float, b: float):
    """非法BM25参数应尽早报错，而不是在查询阶段产生异常分数。"""

    with pytest.raises(ValueError):
        BM25Index([], k1=k1, b=b)
