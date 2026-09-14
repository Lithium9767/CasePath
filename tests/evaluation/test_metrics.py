import math

import pytest

from casepath.evaluation.metrics import aggregate_scores, score_ranking, validate_k


def test_binary_and_graded_metrics_have_distinct_meanings():
    result = score_ranking(
        ["b", "x", "a"], {"a": 3, "b": 1}, candidate_ids=["a", "b", "x"], k=[1, 2, 3]
    )
    assert result["at_k"]["1"]["recall"] == 0.5
    assert result["at_k"]["1"]["ndcg"] == pytest.approx(1 / 7)
    assert result["at_k"]["3"]["recall"] == 1
    assert result["at_k"]["3"]["ndcg"] == pytest.approx(
        (1 + 7 / math.log2(4)) / (7 + 1 / math.log2(3))
    )


def test_perfect_ranking_and_order_penalty():
    kwargs = {"qrels": {"a": 3, "b": 2, "c": 1}, "candidate_ids": list("abc"), "k": [3]}
    assert score_ranking(list("abc"), **kwargs)["at_k"]["3"]["ndcg"] == 1
    assert score_ranking(list("cba"), **kwargs)["at_k"]["3"]["ndcg"] < 1


def test_exclude_before_top_k_and_remove_self_from_denominator():
    result = score_ranking(
        ["self", "a", "b"],
        {"self": 3, "a": 3, "b": 1},
        candidate_ids=["self", "a", "b"],
        k=[1],
        exclude_ids=["self"],
    )
    assert result["positive_count"] == 2
    assert result["at_k"]["1"]["recall"] == 0.5
    assert result["at_k"]["1"]["ndcg"] == 1


def test_empty_ranking_is_zero_not_skipped():
    result = score_ranking([], {"a": 1}, candidate_ids=["a"], k=[20])
    assert result["status"] == "scored"
    assert result["at_k"]["20"]["recall"] == 0
    assert result["at_k"]["20"]["ndcg"] == 0


@pytest.mark.parametrize("qrels", [{}, {"a": 0}])
def test_no_positives_is_not_fabricated_perfect_or_zero_score(qrels):
    result = score_ranking(["a"], qrels, candidate_ids=["a"], k=[1])
    assert result["status"] == "no_positive_judgments"
    assert result["at_k"]["1"]["recall"] is None
    assert aggregate_scores([result], k=[1])["macro_at_k"]["1"]["ndcg"] is None


@pytest.mark.parametrize("k", [[], [0], [-1], [True], [1.0], [1, 1]])
def test_invalid_k_is_rejected(k):
    with pytest.raises(ValueError):
        validate_k(k)


@pytest.mark.parametrize("grade", [-1, 4, 1.0, True, "1", None])
def test_invalid_grades_are_rejected(grade):
    with pytest.raises(ValueError):
        score_ranking(["a"], {"a": grade}, candidate_ids=["a"])


@pytest.mark.parametrize(
    "changes",
    [
        {"ranking": ["a", "a"]},
        {"ranking": ["a", "missing"]},
        {"qrels": {"missing": 1}},
        {"candidate_ids": ["a", "a"]},
        {"candidate_ids": "a"},
        {"exclude_ids": ["missing"]},
        {"exclude_ids": ["a", "a"]},
        {"ranking": [""]},
    ],
)
def test_invalid_ids_rejected_including_beyond_k(changes):
    args = {"ranking": ["a"], "qrels": {"a": 1}, "candidate_ids": ["a"], "k": [1]}
    args.update(changes)
    with pytest.raises((ValueError, TypeError)):
        score_ranking(**args)


def test_macro_means_report_unscorable_and_count_empty_rankings():
    good = score_ranking(["a"], {"a": 1}, candidate_ids=["a"], k=[1])
    bad = score_ranking([], {"a": 1}, candidate_ids=["a"], k=[1])
    unknown = score_ranking([], {}, candidate_ids=["a"], k=[1])
    result = aggregate_scores([good, bad, unknown], k=[1])
    assert result["query_count"] == 3
    assert result["scored_query_count"] == 2
    assert result["no_positive_query_count"] == 1
    assert result["macro_at_k"]["1"] == {"recall": 0.5, "ndcg": 0.5}
