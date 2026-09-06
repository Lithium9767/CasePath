"""P4 实现区：共享案例分化指标。E 与解释必须复用本结果，不得各算一套。

小样本互信息不进入排序。未知比例单独报告，不把缺失当成高分化。
"""

from __future__ import annotations

import math
from collections import Counter

from casepath.adapters.retrieval_catalog import RetrievalCatalog, current_round
from casepath.contracts import (
    ComparisonBundle,
    ConditionComparison,
    ConditionStatus,
    QueryState,
    RetrievalBundle,
    ScoreComponent,
)

_KNOWN = (
    ConditionStatus.SATISFIED,
    ConditionStatus.NOT_SATISFIED,
    ConditionStatus.CONFLICTING,
)


def _entropy(counts: Counter[ConditionStatus]) -> float:
    known = [counts[status] for status in _KNOWN if counts[status]]
    total = sum(known)
    if total <= 0:
        return 0.0
    entropy = 0.0
    for value in known:
        probability = value / total
        entropy -= probability * math.log2(probability)
    return entropy


def _disagreement(counts: Counter[ConditionStatus]) -> float:
    known = [counts[status] for status in _KNOWN]
    total = sum(known)
    if total < 2:
        return 0.0
    pairs = total * (total - 1) / 2
    same = sum(value * (value - 1) / 2 for value in known if value)
    return (pairs - same) / pairs if pairs else 0.0


class ConditionComparator:
    def __init__(self, catalog: RetrievalCatalog) -> None:
        self.catalog = catalog

    def compare(self, state: QueryState, bundle: RetrievalBundle) -> ComparisonBundle:
        cache = current_round()
        case_ids = [
            item.object_id
            for item in (
                *bundle.support_case_refs,
                *bundle.limiting_case_refs,
                *bundle.boundary_case_refs,
            )
        ]
        comparisons: list[ConditionComparison] = []
        for condition in state.condition_states:
            counts: Counter[ConditionStatus] = Counter()
            support: list[str] = []
            limiting: list[str] = []
            boundary: list[str] = []
            for case_id in case_ids:
                assessments = ()
                if cache is not None:
                    assessments = cache.assessments_by_case.get(case_id, ())
                if not assessments:
                    assessments = (
                        self.catalog.cases.get(case_id).assessments
                        if case_id in self.catalog.cases
                        else ()
                    )
                match = next(
                    (item for item in assessments if item.condition_id == condition.condition_id),
                    None,
                )
                if match is None:
                    counts[ConditionStatus.UNKNOWN] += 1
                    continue
                counts[match.status] += 1
                if case_id in {item.object_id for item in bundle.support_case_refs}:
                    support.append(case_id)
                elif case_id in {item.object_id for item in bundle.limiting_case_refs}:
                    limiting.append(case_id)
                elif case_id in {item.object_id for item in bundle.boundary_case_refs}:
                    boundary.append(case_id)
            known_n = sum(counts[status] for status in _KNOWN)
            entropy = _entropy(counts)
            disagree = _disagreement(counts)
            unknown_rate = counts[ConditionStatus.UNKNOWN] / max(1, sum(counts.values()))
            heuristic = entropy * (1.0 - unknown_rate)
            comparisons.append(
                ConditionComparison(
                    condition_id=condition.condition_id,
                    status_counts=dict(counts),
                    condition_entropy=entropy,
                    outcome_mutual_information=0.0,
                    expected_information_gain=heuristic,
                    expected_rank_change=0.0,
                    expected_branch_reduction=0.0,
                    case_disagreement_score=disagree,
                    rule_condition_discriminativeness=min(
                        1.0, (entropy / 2.0) * (1.0 - unknown_rate)
                    ),
                    support_case_ids=list(dict.fromkeys(support)),
                    limiting_case_ids=list(dict.fromkeys(limiting)),
                    boundary_case_ids=list(dict.fromkeys(boundary)),
                    score_components=[
                        ScoreComponent(
                            name="known_cases",
                            value=float(known_n),
                            explanation="具有明确认定的案例数；互信息未实现，始终为0",
                        ),
                        ScoreComponent(
                            name="unknown_coverage",
                            value=unknown_rate,
                            explanation="案例条件未知比例，单独报告",
                        ),
                        ScoreComponent(
                            name="entropy_coverage_heuristic",
                            value=heuristic,
                            explanation="条件熵×已知覆盖率启发式，不是模拟回答后的信息增益",
                        ),
                    ],
                )
            )
        ranked = sorted(
            comparisons,
            key=lambda item: (
                -item.condition_entropy,
                -item.case_disagreement_score,
                item.condition_id,
            ),
        )
        return ComparisonBundle(
            condition_comparisons=comparisons,
            ranked_condition_ids=[item.condition_id for item in ranked],
            degraded=True,
            degradation_reason="分化基线：互信息未实现且始终为0；expected_information_gain 为熵×覆盖率启发式，未模拟回答后不确定性下降。",
        )
