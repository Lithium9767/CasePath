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
    # 统计分类包含“冲突”：这里的 known 不代表该条件一定满足或真相已经确定。
    # UNKNOWN 不进入熵/分歧的状态分布，缺失程度另用 unknown_rate 报告。
    ConditionStatus.SATISFIED,
    ConditionStatus.NOT_SATISFIED,
    ConditionStatus.CONFLICTING,
)


def _entropy(counts: Counter[ConditionStatus]) -> float:
    # 条件熵 H = -Σ p*log2(p)，p 是某状态在参与统计案例中的比例。
    # 全部案例同一状态则为 0；状态分布越分散则越高；不是用户胜诉概率。
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
    # 从参与统计的案例中任取两件：状态不同的配对数 / 所有配对数。
    # pairs=C(total,2)，same=ΣC(各状态数量,2)，所以分歧率=(pairs-same)/pairs。
    # 不足两件时返回 0 只是数值约定，不能据此宣称“案例一致”。
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
        # C 未能确定支持/限制/边界角色，不代表该案例所有条件证据都不可用。
        # 当前只补入本轮缓存中至少带 span/finding 引用的未定案例，不扫描整个案例库。
        for case_id in sorted(cache.undetermined_case_ids):
            assessments = cache.assessments_by_case.get(case_id, ())
            if any(item.span_ids or item.finding_ids for item in assessments):
                ordered.append(case_id)
    return list(dict.fromkeys(ordered))


class ConditionComparator:
    def __init__(self, catalog: RetrievalCatalog) -> None:
        self.catalog = catalog

    def compare(self, state: QueryState, bundle: RetrievalBundle) -> ComparisonBundle:
        # 【D 总览】输入 B 的条件状态与 C 的检索结果，输出逐条件的案例统计给 E/解释层。
        # C 关注“哪些案例可比较、属于什么角色”，D 关注“它们在各条件上分布如何”。
        # 本函数不调用 LLM、不重新检索，也不改变用户条件状态或重新判定案例角色。
        import time

        from casepath.adapters.diagnostics.stage_events import emit_stage_event

        started = time.perf_counter()
        succeeded = False
        cache = current_round()
        # cache 是 C 保存的本轮图认定/候选信息；scope 是 A 确定的活动规则条件范围。
        # 它们是请求上下文，不是额外读取一次 Neo4j。
        scope = current_scope()
        case_ids = _collect_comparison_cases(bundle, cache)
        active = set(scope.active_condition_ids) if scope is not None else None
        comparisons: list[ConditionComparison] = []
        try:
            for condition in state.condition_states:
                # 【D 1：按条件逐行比较】相当于构造“条件 × 案例”矩阵。
                # 行由用户状态中的 condition_id 指定，单元格是历史案例对此条件的认定，
                # 并不是把用户的 SATISFIED/UNKNOWN 重复计入案例数量。
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
                    # 【D 2：取案例认定】优先本轮缓存；该案例缓存为空才回退本地目录。
                    # 注意这是按案例是否有缓存回退，并非逐条件补齐缺失认定。
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
                        # 与 C 复用同一聚合函数：同一案例有多条条件认定时先合成一个结果，
                        # 避免把多条引句算成多件案例，或只取第一条导致 C/D 状态不一致。
                        case_id=case_id,
                        condition_id=condition.condition_id,
                        assessments=assessments,
                    )
                    if not summary.has_usable_evidence:
                        # 找不到可用证据记 UNKNOWN，不当成“不满足”，也不跳过缺失统计。
                        counts[ConditionStatus.UNKNOWN] += 1
                        continue
                    counts[summary.status] += 1
                    # 下列三份 ID 列表只继承 C 已分配的角色，并要求本条件有可用证据。
                    # 未定角色案例可以贡献 counts，但不会在这里被自动升级成支持案例。
                    if case_id in {item.object_id for item in bundle.support_case_refs}:
                        support.append(case_id)
                    elif case_id in {item.object_id for item in bundle.limiting_case_refs}:
                        limiting.append(case_id)
                    elif case_id in {item.object_id for item in bundle.boundary_case_refs}:
                        boundary.append(case_id)
                known_n = sum(counts[status] for status in _KNOWN)
                # 【D 3：统计】数量、熵、不同状态配对比例、UNKNOWN 比例分别回答不同问题。
                # 分母 max(1,...) 防止空案例池除零；空池应结合 availability 解读。
                entropy = _entropy(counts)
                disagree = _disagreement(counts)
                unknown_rate = counts[ConditionStatus.UNKNOWN] / max(1, sum(counts.values()))
                heuristic = entropy * (1.0 - unknown_rate)
                # 用覆盖率抑制大量未知时的熵，但这只是工程启发式，没有模拟回答。
                if known_n == 0:
                    availability = "NO_EVIDENCE"
                elif known_n == 1:
                    availability = "SINGLE_CASE"
                else:
                    availability = "DESCRIPTIVE_COMPARISON"
                comparisons.append(
                    # 【D 4：打包一行】保留原始计数，让前端能区分无证据/单例/多例。
                    # 互信息、预期排名变化、分支缩减目前是未实现占位，0 不代表实测无影响。
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
                            # 这是带上限的启发式缩放，不是经标注集校准的区分概率。
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
                # 【D 5：给出条件排序】先按熵降序，再按分歧率降序，同分按 ID 稳定排序。
                # 这里没有只保留“用户未知”的条件；E 还要筛选缺口、历史和可回答性。
                # 非活动占位行也在这个列表中，不能直接把第一项当成最终追问。
                comparisons,
                key=lambda item: (
                    -item.condition_entropy,
                    -item.case_disagreement_score,
                    item.condition_id,
                ),
            )
            undetermined_n = len(cache.undetermined_case_ids) if cache is not None else 0
            known_total = sum(
                # 日志总量是“案例—条件单元格”数，不是去重后的案例件数。
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
                # 【D 输出】比较明细 + 条件排名 + 降级说明；始终 degraded=True 是因为
                # 当前仍属描述性统计基线，不能包装成完整的信息增益/互信息算法。
                condition_comparisons=comparisons,
                ranked_condition_ids=[item.condition_id for item in ranked],
                degraded=True,
                degradation_reason=(
                    "分化基线：互信息始终为0；expected_information_gain 为熵×覆盖率启发式。"
                    f"未定角色候选 {undetermined_n} 件在有认定时可参与统计。"
                ),
            )
        finally:
            # 成功或异常都记本阶段耗时及计数；succeeded 用来区分失败，不记录用户原话。
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
