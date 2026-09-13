"""P4 实现区：共享案例分化指标。E 与解释必须复用本结果，不得各算一套。

小样本互信息不进入排序。未知比例单独报告，不把缺失当成高分化。
未定角色案例在有可用认定时可参与统计，不再等同于没有案例。
"""

from __future__ import annotations

import math
from collections import Counter

from casepath.adapters.conditions.case_condition_evidence import aggregate_case_condition
from casepath.adapters.retrieval.retrieval_catalog import (
    RetrievalCatalog,
    current_round,
    current_scope,
)
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


def _collect_comparison_cases(bundle: RetrievalBundle, cache) -> list[str]:
    """角色案例 ∪ 有证据的未定角色案例；去重且顺序稳定。"""
    ordered = [
        item.object_id
        for item in (
            *bundle.support_case_refs,
            *bundle.limiting_case_refs,
            *bundle.boundary_case_refs,
        )
    ]
    if cache is not None:
        for case_id in sorted(cache.undetermined_case_ids):
            assessments = cache.assessments_by_case.get(case_id, ())
            if any(item.span_ids or item.finding_ids for item in assessments):
                ordered.append(case_id)
    return list(dict.fromkeys(ordered))


class ConditionComparator:
    def __init__(self, catalog: RetrievalCatalog) -> None:
        self.catalog = catalog

    def compare(self, state: QueryState, bundle: RetrievalBundle) -> ComparisonBundle:
        import time

        from casepath.adapters.diagnostics.stage_events import emit_stage_event

        started = time.perf_counter()
        succeeded = False
        cache = current_round()
        scope = current_scope()
        case_ids = _collect_comparison_cases(bundle, cache)
        active = set(scope.active_condition_ids) if scope is not None else None
        comparisons: list[ConditionComparison] = []
        try:
            for condition in state.condition_states:
                if active is not None and condition.condition_id not in active:
                    # 历史非活动条件：仍输出空统计占位，避免前端断链，但不参与分化排序权重。
                    comparisons.append(
                        ConditionComparison(
                            condition_id=condition.condition_id,
                            status_counts={ConditionStatus.UNKNOWN: 0},
                            condition_entropy=0.0,
                            outcome_mutual_information=0.0,
                            expected_information_gain=0.0,
                            expected_rank_change=0.0,
                            expected_branch_reduction=0.0,
                            case_disagreement_score=0.0,
                            rule_condition_discriminativeness=0.0,
                            score_components=[
                                ScoreComponent(
                                    name="availability",
                                    value=0.0,
                                    explanation="NO_EVIDENCE：条件非本轮活动范围",
                                )
                            ],
                        )
                    )
                    continue
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
                    summary = aggregate_case_condition(
                        case_id=case_id,
                        condition_id=condition.condition_id,
                        assessments=assessments,
                    )
                    if not summary.has_usable_evidence:
                        counts[ConditionStatus.UNKNOWN] += 1
                        continue
                    counts[summary.status] += 1
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
                if known_n == 0:
                    availability = "NO_EVIDENCE"
                elif known_n == 1:
                    availability = "SINGLE_CASE"
                else:
                    availability = "DESCRIPTIVE_COMPARISON"
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
                                name="availability",
                                value=float(known_n),
                                explanation=availability,
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
            undetermined_n = len(cache.undetermined_case_ids) if cache is not None else 0
            known_total = sum(
                sum(item.status_counts.get(status, 0) for status in _KNOWN)
                for item in comparisons
            )
            unknown_total = sum(
                item.status_counts.get(ConditionStatus.UNKNOWN, 0) for item in comparisons
            )
            conflict_total = sum(
                item.status_counts.get(ConditionStatus.CONFLICTING, 0) for item in comparisons
            )
            succeeded = True
            return ComparisonBundle(
                condition_comparisons=comparisons,
                ranked_condition_ids=[item.condition_id for item in ranked],
                degraded=True,
                degradation_reason=(
                    "分化基线：互信息始终为0；expected_information_gain 为熵×覆盖率启发式。"
                    f"未定角色候选 {undetermined_n} 件在有认定时可参与统计。"
                ),
            )
        finally:
            emit_stage_event(
                stage="D_comparison",
                duration_ms=(time.perf_counter() - started) * 1000,
                counts={
                    "conditions": len(comparisons),
                    "cases": len(case_ids),
                    "known_status_total": known_total if "known_total" in locals() else 0,
                    "unknown_status_total": unknown_total if "unknown_total" in locals() else 0,
                    "conflict_status_total": conflict_total if "conflict_total" in locals() else 0,
                    "undetermined_cases": (
                        len(cache.undetermined_case_ids) if cache is not None else 0
                    ),
                },
                reason_codes=[],
                identifiers={},
                succeeded=succeeded,
            )
