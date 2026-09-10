"""P4 实现区：共享案例分化指标。E 与解释必须复用本结果，不得各算一套。

小样本互信息不进入排序。未知比例单独报告，不把缺失当成高分化。
"""

from __future__ import annotations

import math
from collections import Counter

from casepath.adapters.retrieval.retrieval_catalog import RetrievalCatalog, current_round
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
    # H = -Σ p*log2(p)：已计入状态越分散，熵越大；全都同状态则为 0。
    # 这里只统计 _KNOWN（含冲突），UNKNOWN 不混进分歧，另用未知比例报告。
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
    # 分歧率 = 状态不同的案例对数 / 全部案例对数；不是对裁判结果做相关性计算。
    same = sum(value * (value - 1) / 2 for value in known if value)
    return (pairs - same) / pairs if pairs else 0.0


class ConditionComparator:
    def __init__(self, catalog: RetrievalCatalog) -> None:
        self.catalog = catalog

    def compare(self, state: QueryState, bundle: RetrievalBundle) -> ComparisonBundle:
        # 【D】比较对象：用户侧每个条件 × 本轮检索到的支持/限制/边界案例。
        # 不做「用户条件状态 vs 案例」的一一对撞；而是看同一条件在候选案例里的认定怎么分布。
        # 分布越散（熵大、分歧高）→ 该条件越能把案例区分开，供后续追问排序复用。
        # 数据只读本轮图缓存或 catalog.assessments，不调 LLM、不查库。
        cache = current_round()
        # 三类角色案例并成一份公共池；同一 case_id 可能因并集出现多次，后面按角色分桶去重。
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
            # 对该条件：统计案例认定直方图，并把「有明确认定」的案例按角色记下 ID。
            counts: Counter[ConditionStatus] = Counter()
            support: list[str] = []
            limiting: list[str] = []
            boundary: list[str] = []
            for case_id in case_ids:
                # 优先用本轮图路径已取到的认定；否则回落 catalog 深度案例上的 assessments。
                assessments = ()
                if cache is not None:
                    assessments = cache.assessments_by_case.get(case_id, ())
                if not assessments:
                    assessments = (
                        self.catalog.cases.get(case_id).assessments
                        if case_id in self.catalog.cases
                        else ()
                    )
                # 只比对「同一 condition_id」：案例对该条件的 SATISFIED / NOT_SATISFIED / CONFLICTING。
                match = next(
                    (item for item in assessments if item.condition_id == condition.condition_id),
                    None,
                )
                if match is None:
                    # 案例没写这条条件：记 UNKNOWN，计入覆盖率，但不进熵/分歧（避免「缺数据=高分化」）。
                    counts[ConditionStatus.UNKNOWN] += 1
                    continue
                counts[match.status] += 1
                # 仅在「该条件有认定」时才归入角色列表，方便解释「谁支持/谁限制」。
                if case_id in {item.object_id for item in bundle.support_case_refs}:
                    support.append(case_id)
                elif case_id in {item.object_id for item in bundle.limiting_case_refs}:
                    limiting.append(case_id)
                elif case_id in {item.object_id for item in bundle.boundary_case_refs}:
                    boundary.append(case_id)
            # --- 由直方图算出几个分化指标 ---
            known_n = sum(counts[status] for status in _KNOWN)
            entropy = _entropy(counts)  # 已知状态越分散越大；全同则 0
            disagree = _disagreement(counts)  # 已知案例两两状态不同的比例
            unknown_rate = counts[ConditionStatus.UNKNOWN] / max(1, sum(counts.values()))
            # 熵再乘「已知覆盖率」：未知多时折扣，免得只有一两例也看起来很「分化」。
            heuristic = entropy * (1.0 - unknown_rate)
            # expected_information_gain 字段名沿用合同，这里填的是上述启发式，不是真正模拟追问后的增益。
            # outcome_mutual_information / rank_change / branch_reduction 基线未实现，固定 0。
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
                    # 熵最大约 log2(3)≈1.58，这里用 /2 再乘覆盖率并截到 [0,1]，作可解释性标量。
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
        # 分化排序键：熵 ↓ → 分歧率 ↓ → condition_id；不是追问最终序，也不用 heuristic。
        ranked = sorted(
            comparisons,
            key=lambda item: (
                -item.condition_entropy,
                -item.case_disagreement_score,
                item.condition_id,
            ),
        )
        # degraded=True：标明这是分化基线，下游不要当成完整互信息/信息增益方案。
        return ComparisonBundle(
            condition_comparisons=comparisons,
            ranked_condition_ids=[item.condition_id for item in ranked],
            degraded=True,
            degradation_reason="分化基线：互信息未实现且始终为0；expected_information_gain 为熵×覆盖率启发式，未模拟回答后不确定性下降。",
        )
