"""Strict ranked-retrieval metrics with explicit unscorable-query handling."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence


def validate_k(values: Iterable[int]) -> tuple[int, ...]:
    values = tuple(values)
    if not values or any(type(value) is not int or value <= 0 for value in values):
        raise ValueError("k must contain positive integers")
    if len(set(values)) != len(values):
        raise ValueError("duplicate k values")
    return tuple(sorted(values))


def _ids(values: Iterable[str], label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{label} must be an ID collection")
    result = tuple(values)
    if any(not isinstance(value, str) or not value.strip() for value in result):
        raise ValueError(f"invalid {label} IDs")
    if len(result) != len(set(result)):
        raise ValueError(f"duplicate {label} IDs")
    return result


def score_ranking(
    ranking: Sequence[str],
    qrels: Mapping[str, int],
    *,
    candidate_ids: Iterable[str],
    k: Iterable[int] = (5, 10, 20),
    exclude_ids: Iterable[str] = (),
) -> dict:
    """Recall uses grade > 0; nDCG uses (2**grade - 1)/log2(rank+1).

    Unjudged objects contribute zero *under these qrels*, not a claim of true
    irrelevance. Exclusions are applied before cutting the ranking and the ideal
    ranking. Invalid IDs and duplicates, even beyond k, are rejected.
    """
    ks = validate_k(k)
    candidates = set(_ids(candidate_ids, "candidate"))
    ranked = _ids(ranking, "ranking")
    excluded = set(_ids(exclude_ids, "excluded"))
    if not isinstance(qrels, Mapping):
        raise TypeError("qrels must be an ID to grade mapping")
    _ids(qrels, "qrels")
    if any(type(grade) is not int or not 0 <= grade <= 3 for grade in qrels.values()):
        raise ValueError("relevance grades must be integers in 0..3")
    if (set(ranked) | set(qrels) | excluded) - candidates:
        raise ValueError("ranking, qrels or exclusions reference unknown candidates")
    ranked = tuple(oid for oid in ranked if oid not in excluded)
    grades = {oid: grade for oid, grade in qrels.items() if oid not in excluded}
    positive_count = sum(grade > 0 for grade in grades.values())
    ideal = sorted((grade for grade in grades.values() if grade > 0), reverse=True)
    scores = {}
    for limit in ks:
        top = ranked[:limit]
        hits = sum(grades.get(oid, 0) > 0 for oid in top)
        dcg = math.fsum(
            (2 ** grades.get(oid, 0) - 1) / math.log2(rank + 1)
            for rank, oid in enumerate(top, start=1)
        )
        idcg = math.fsum(
            (2**grade - 1) / math.log2(rank + 1)
            for rank, grade in enumerate(ideal[:limit], start=1)
        )
        scores[str(limit)] = {
            "recall": hits / positive_count if positive_count else None,
            "ndcg": dcg / idcg if idcg else None,
            "relevant_retrieved": hits,
            "returned": len(top),
            "judged_returned": sum(oid in grades for oid in top),
            "unjudged_returned": sum(oid not in grades for oid in top),
        }
    return {
        "status": "scored" if positive_count else "no_positive_judgments",
        "positive_count": positive_count,
        "judged_count": len(grades),
        "candidate_count": len(candidates - excluded),
        "excluded_count": len(excluded),
        "ranking_count": len(ranked),
        "at_k": scores,
    }


def aggregate_scores(results: Sequence[dict], *, k: Iterable[int]) -> dict:
    """Query-macro means; unscorable queries remain visible in the denominator report."""
    ks = validate_k(k)
    eligible = [result for result in results if result["status"] == "scored"]
    return {
        "query_count": len(results),
        "scored_query_count": len(eligible),
        "no_positive_query_count": len(results) - len(eligible),
        "macro_at_k": {
            str(limit): {
                metric: (
                    math.fsum(result["at_k"][str(limit)][metric] for result in eligible)
                    / len(eligible)
                    if eligible
                    else None
                )
                for metric in ("recall", "ndcg")
            }
            for limit in ks
        },
    }
