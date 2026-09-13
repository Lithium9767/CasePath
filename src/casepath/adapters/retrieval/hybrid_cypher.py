"""P4 实现区：HybridCypher 规则/案例检索。

混合召回＋固定图扩展，并保留规则条件驱动的独立图召回。不提供四选一选择器。
请求级 RetrievalContext 用 contextvars 传递，禁止写入共享 retriever 字段。
图路径只返回投影中真实存在的中间节点和有向边；全文命中不编造路径。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal, Protocol

from casepath.adapters.conditions.condition_logic import group_truth
from casepath.adapters.retrieval.graph_navigator import (
    GraphHit,
    GraphNavigator,
    MemoryGraphNavigator,
)
from casepath.adapters.retrieval.retrieval_catalog import (
    CaseAssessment,
    CaseDocument,
    RetrievalCatalog,
    RetrievalContext,
    RoundEvidence,
    bind_context,
    require_context,
    scored_reference,
    set_round,
    weighted_rrf,
)
from casepath.application.errors import RetrieverUnavailable
from casepath.contracts import (
    ConditionStatus,
    DecisionStatus,
    QueryState,
    RetrievalBundle,
    RetrievalPath,
    ScoreComponent,
    ScoredReference,
)
from casepath.ports import LegalGraphGateway

# 供外部与测试继续从本模块导入上下文类型。
__all__ = [
    "HybridCandidateSearch",
    "HybridCypherCaseRetriever",
    "HybridCypherRuleRetriever",
    "HybridCypherSettings",
    "RetrievalContext",
]

_DECISIVE = {ConditionStatus.SATISFIED, ConditionStatus.NOT_SATISFIED}


@dataclass(frozen=True)
class HybridCypherSettings:
    """P4 内部参数，不是公共 API 合同；参数需经开发集检验。"""

    rule_top_k: int = 10
    # 三阶段预算独立：每路先召回，再截取重排池，最后按角色少量输出。
    case_recall_top_k: int = 30
    case_rerank_pool_size: int = 20
    case_role_top_k: int = 3
    per_condition_limit: int = 30
    rrf_k: int = 60
    fulltext_weight: float = 1.0
    vector_weight: float = 1.0
    claim_weight: float = 0.8
    graph_weight: float = 1.0
    cites_weight: float = 0.5

    def __post_init__(self) -> None:
        numeric = [
            self.rule_top_k,
            self.case_recall_top_k,
            self.case_rerank_pool_size,
            self.case_role_top_k,
            self.per_condition_limit,
            self.rrf_k,
        ]
        if min(numeric) <= 0:
            raise ValueError("HybridCypher limits must be positive")
        weights = [
            self.fulltext_weight,
            self.vector_weight,
            self.claim_weight,
            self.graph_weight,
            self.cites_weight,
        ]
        if min(weights) < 0:
            raise ValueError("HybridCypher weights must be non-negative")


class HybridCandidateSearch(Protocol):
    """P4 注入索引实现；同一次搜索的全文与向量结果必须处于同一候选对象空间。"""

    def search(
        self,
        text: str,
        *,
        target: Literal["rule", "case"],
        context: RetrievalContext,
        top_k: int,
    ) -> list[ScoredReference]: ...


def _query_text(state: QueryState) -> str:
    # 把初始问题、累计原话和候选诉求拼成检索文本；这里不生成新的法律事实。
    parts = [state.initial_query]
    parts.extend(item.text for item in state.user_facts)
    parts.extend(item.claim_type for item in state.candidate_claims)
    parts.extend(item.requested_remedy for item in state.candidate_claims)
    return "\n".join(part for part in parts if part)


def _claim_rank(state: QueryState, catalog: RetrievalCatalog) -> list[str]:
    # 当前是 claim_types 在问题/诉求文本中的包含匹配，按命中个数排序，并非训练的分类模型。
    claims = [item.claim_type for item in state.candidate_claims] + [state.initial_query]
    blob = "\n".join(claims)
    scored: list[tuple[int, str]] = []
    for rule in catalog.rules.values():
        overlap = sum(1 for claim in rule.claim_types if claim and claim in blob)
        if overlap:
            scored.append((-overlap, rule.rule_id))
    scored.sort()
    return [rule_id for _, rule_id in scored]


def _raw_scores(refs: list[ScoredReference]) -> dict[tuple[str, str], float]:
    values: dict[tuple[str, str], float] = {}
    for item in refs:
        for component in item.score_components:
            values[(item.object_id, component.name)] = component.value
        if not item.score_components:
            values[(item.object_id, "raw")] = item.score
    return values


def _valid_path(nodes: tuple[str, ...] | list[str], edges: tuple[str, ...] | list[str]) -> bool:
    return len(nodes) >= 2 and len(edges) == len(nodes) - 1 and all(nodes) and all(edges)


# 先从多个通道召回候选，再用图关系扩展，最后统一融合、重排。
class HybridCypherRuleRetriever:
    """模块 A：全文＋向量规则召回，叠加请求权匹配和规则图扩展。"""

    def __init__(
        self,
        graph: LegalGraphGateway | GraphNavigator,
        search: HybridCandidateSearch,
        settings: HybridCypherSettings | None = None,
        *,
        catalog: RetrievalCatalog,
        navigator: GraphNavigator | None = None,
    ) -> None:
        self.graph = graph
        self.search = search
        self.settings = settings or HybridCypherSettings()
        self.catalog = catalog
        self.navigator = navigator or (
            graph if isinstance(graph, GraphNavigator) else MemoryGraphNavigator(catalog)
        )

    def retrieve(self, state: QueryState) -> list[ScoredReference]:
        # 【A 阅读顺序】搜索规则 → 用结果作图扩展种子 → 各通道排名融合 → 返回 top_k 引用。
        # catalog 存规则正文等内容；ScoredReference 主要携带 ID、得分及证据/路径引用。
        # 拿去文本内容
        context = self.catalog.context()
        # 绑定这轮请求
        bind_context(context)
        # 固定本轮发布版本，供后续 C 读取；不是把用户上下文写进共享 retriever 实例。
        if context.release_id != self.catalog.release_id:
            raise RetrieverUnavailable("规则索引与本轮图发布不一致")
        # 从query中提取文本
        query = _query_text(state)
        # 空拒绝
        if not query.strip():
            raise RetrieverUnavailable("查询文本为空，拒绝返回空成功")
        try:
            # 第一通道：全文 / 向量搜索 BM25 + 向量余弦
            fulltext = self.search.search(
                query,
                target="rule",
                context=context,
                top_k=min(self.settings.rule_top_k, max(1, len(self.catalog.rules))),
            )
        except Exception as exc:
            raise RetrieverUnavailable("规则全文通道不可用") from exc
        # 请求权召回
        claim_ids = _claim_rank(state, self.catalog)
        # fulltext 是历史变量名：注入 VectorHybridSearch 时里面同时有 BM25 和向量结果。
        # 作为图搜索种子
        seed_ids = list(dict.fromkeys([item.object_id for item in fulltext] + claim_ids))
        neighbors = self.navigator.neighbor_rules(context, seed_ids)
        # 图扩展围绕已命中的种子找共享条件的规则，不从全图任意传播，也不使用 PageRank。
        graph_ids = list(dict.fromkeys(item.rule_id for item in neighbors))
        # 整理成四个召回通道
        ranked = {
            "bm25": [
                item.object_id for item in fulltext if "vector" not in item.retrieval_channels
            ],
            "vector": [item.object_id for item in fulltext if "vector" in item.retrieval_channels],
            "claim": claim_ids,
            "graph": graph_ids,
        }
        fused = weighted_rrf(
            ranked,
            weights={
                "bm25": self.settings.fulltext_weight, # 1.0
                "vector": self.settings.vector_weight, # 1.0
                "claim": self.settings.claim_weight, # 0.8
                # 邻接通道没有查询相关度，不让按 ID 排序的首位与直接命中平权。所有乘以0.25
                "graph": 0.25 * self.settings.graph_weight, # 1.0
            },
            k=self.settings.rrf_k,
        )
        raw = _raw_scores(fulltext)
        # 排序用融合分；原始 BM25/余弦分另存为解释字段，不能当成同一量纲直接相加。
        neighbor_paths: dict[str, list[RetrievalPath]] = defaultdict(list)
        for hit in neighbors:
            if not _valid_path(hit.path_nodes, hit.path_edges):
                continue
            neighbor_paths[hit.rule_id].append(
                RetrievalPath(
                    node_ids=list(hit.path_nodes),
                    edge_types=list(hit.path_edges),
                    score=fused.get(hit.rule_id, 0.0),
                    source_span_ids=[],
                )
            )
        results: list[ScoredReference] = []
        for rule_id, score in sorted(fused.items(), key=lambda item: (-item[1], item[0])):
            rule = self.catalog.rules.get(rule_id)
            if rule is None:
                continue
            channels = [name for name, ids in ranked.items() if rule_id in ids]
            components = [
                ScoreComponent(name="rrf", value=score, explanation="加权RRF融合，缺席通道为0"),
            ]
            if (rule_id, "bm25") in raw:
                components.append(
                    ScoreComponent(
                        name="bm25", value=raw[(rule_id, "bm25")], explanation="全文原始分"
                    )
                )
            if (rule_id, "vector") in raw:
                components.append(
                    ScoreComponent(
                        name="vector",
                        value=raw[(rule_id, "vector")],
                        explanation="向量原始余弦分数",
                    )
                )
            reasons = []
            if rule_id in ranked["claim"]:
                reasons.append("请求权类型与规则 claim_types 重合")
            if rule_id in ranked["graph"]:
                reasons.append("与已召回规则共享条件邻接")
            if rule_id in ranked["bm25"]:
                reasons.append("规则卡片全文命中")
            paths = neighbor_paths.get(rule_id, []) if rule_id in ranked["graph"] else []
            results.append(
                scored_reference(
                    rule_id,
                    score,
                    channels=channels,
                    components=components,
                    reasons=reasons or ["融合后保留"],
                    span_ids=list(rule.span_ids),
                    paths=paths,
                )
            )
        # 图扩展保留为低权重边界候选，不能靠任意邻接顺序占据解释头部。
        limit = min(self.settings.rule_top_k, len(results))
        limited = results[:limit]
        # 无外层用途分层时写入默认 ACTIVE scope；RelevantRuleRetriever 会覆盖。
        from casepath.adapters.retrieval.rule_relevance import bind_default_scope_from_refs

        bind_default_scope_from_refs(limited, self.catalog)
        return limited


def _known_states(state: QueryState) -> dict[str, ConditionStatus]:
    return {item.condition_id: item.status for item in state.condition_states}


def _condition_roles(catalog: RetrievalCatalog, condition_id: str, rule_ids: set[str]) -> list[str]:
    roles: list[str] = []
    for rule_id in catalog.rule_ids_by_condition.get(condition_id, ()):
        if rule_id not in rule_ids:
            continue
        rule = catalog.rules[rule_id]
        for group in rule.groups:
            if condition_id in group.member_ids:
                roles.append(group.role)
    return roles


@dataclass(frozen=True)
class CaseComparison:
    """同一条件空间上的比较记录，不将差异转换为负分。"""

    same: tuple[str, ...]
    different: tuple[str, ...]
    unknown: tuple[str, ...]
    core_agree: int
    core_conflict: int

    @property
    def coverage(self) -> float:
        total = len(self.same) + len(self.different) + len(self.unknown)
        return (len(self.same) + len(self.different)) / total if total else 0.0


def _compare_conditions(
    case: CaseDocument,
    known: dict[str, ConditionStatus],
    catalog: RetrievalCatalog,
    rule_ids: set[str],
) -> CaseComparison:
    # B 提供用户状态；案例认定经共享聚合，不取第一条。
    from casepath.adapters.conditions.case_condition_evidence import aggregate_case_condition
    from casepath.adapters.retrieval.retrieval_catalog import current_scope

    scope = current_scope()
    if scope is not None:
        allowed = {
            key
            for rid in rule_ids
            if rid in scope.active_rule_ids and rid in catalog.rules
            for key in catalog.rules[rid].condition_ids
        }
    else:
        allowed = {
            key
            for rid in rule_ids
            if rid in catalog.rules
            for key in catalog.rules[rid].condition_ids
        }
    same, different, unknown = [], [], []
    for key in sorted(allowed):
        user = known.get(key, ConditionStatus.UNKNOWN)
        summary = aggregate_case_condition(
            case_id=case.case_id,
            condition_id=key,
            assessments=case.assessments,
        )
        other = summary.status if summary.has_usable_evidence else ConditionStatus.UNKNOWN
        if user not in _DECISIVE or other not in _DECISIVE:
            unknown.append(key)
        elif user == other:
            same.append(key)
        else:
            different.append(key)
    agree, conflict = _core_group_comparison(case, known, catalog, rule_ids)
    return CaseComparison(tuple(same), tuple(different), tuple(unknown), agree, conflict)


def _core_group_comparison(
    case: CaseDocument,
    known: dict[str, ConditionStatus],
    catalog: RetrievalCatalog,
    rule_ids: set[str],
) -> tuple[int, int]:
    """先按组求值再比较，不将 ANY 中非决定性的单项差异计为冲突。"""
    from casepath.adapters.conditions.case_condition_evidence import aggregate_case_condition

    member_ids = {
        key
        for rule_id in rule_ids
        if rule_id in catalog.rules
        for group in catalog.rules[rule_id].groups
        if group.role == "CORE"
        for key in group.member_ids
    }
    case_states: dict[str, ConditionStatus] = {}
    for key in member_ids:
        summary = aggregate_case_condition(
            case_id=case.case_id,
            condition_id=key,
            assessments=case.assessments,
        )
        if summary.has_usable_evidence:
            case_states[key] = summary.status
    agree = conflict = 0
    for rule_id in sorted(rule_ids):
        rule = catalog.rules.get(rule_id)
        if rule is None:
            continue
        for group in rule.groups:
            if group.role != "CORE":
                continue
            user_value = group_truth(
                # 必须先算 ALL/ANY 的组结果。例如 ANY 中一项否定、另一项满足，整组仍为真。
                # group_truth 返回 True/False/None，None 表示不足以判断，不是 False。
                group.operator,
                [known.get(key, ConditionStatus.UNKNOWN) for key in group.member_ids],
            )
            case_value = group_truth(
                group.operator,
                [case_states.get(key, ConditionStatus.UNKNOWN) for key in group.member_ids],
            )
            if user_value is True and case_value is True:
                agree += 1
            elif user_value is not None and case_value is not None and user_value != case_value:
                conflict += 1
    return agree, conflict


def _undetermined_reason(
    *,
    has_assessments: bool,
    has_usable_evidence: bool,
    rule_linked: bool,
    claim_aligned: bool,
) -> str:
    if not has_assessments:
        return "NO_ASSESSMENT"
    if not has_usable_evidence:
        return "INSUFFICIENT_EVIDENCE"
    if not rule_linked:
        return "NO_REASONING_LINK"
    if not claim_aligned:
        return "TARGET_MISMATCH"
    return "INSUFFICIENT_EVIDENCE"


def _assign_role(
    case: CaseDocument,
    known: dict[str, ConditionStatus],
    rule_ids: set[str],
    catalog: RetrievalCatalog,
    user_claims: set[str],
    comparison: CaseComparison | None = None,
) -> tuple[str | None, list[str], list[str], str | None]:
    """支持/限制必须结合目标规则、请求、决定性条件差异和理由依据。

    未知、不适用不得当成明确相反条件。不能仅因图通道或单条件一致而标成支持。
    返回 (role, reasons, spans, undetermined_reason_code)。
    """

    if not case.assessments:
        # 角色判断比召回更严格：文本相似但没有条件认定时，允许召回，但角色保持未定。
        return None, [], [], "NO_ASSESSMENT"
    case_claims = {item.claim_type for item in case.claims}
    if user_claims and case_claims:
        claim_aligned = bool(user_claims & case_claims)
    else:
        claim_aligned = not user_claims
    applied = set(case.applied_rule_ids) & rule_ids
    # APPLIES 是裁判说理实际适用；INVOKES 只是请求引用，后者不能单独满足支持角色门槛。
    invoked = set(case.invoked_rule_ids) & rule_ids
    rule_linked = bool(applied or invoked)
    aligned_claim_ids = {
        item.claim_id for item in case.claims if not user_claims or item.claim_type in user_claims
    }
    rejected = any(
        item.status is DecisionStatus.REJECTED and item.claim_id in aligned_claim_ids
        for item in case.decisions
    )
    diagnostic_diff = 0
    spans: list[str] = []
    reasons: list[str] = []
    has_usable_evidence = False
    for assessment in case.assessments:
        user_status = known.get(assessment.condition_id, ConditionStatus.UNKNOWN)
        has_evidence = bool(assessment.span_ids or assessment.finding_ids)
        if has_evidence:
            has_usable_evidence = True
        roles = _condition_roles(catalog, assessment.condition_id, rule_ids)
        core = any(role == "CORE" for role in roles)
        diagnostic = any(role == "DIAGNOSTIC" for role in roles)
        if assessment.status not in _DECISIVE:
            continue
        if user_status not in _DECISIVE:
            if diagnostic and assessment.status is ConditionStatus.NOT_SATISFIED and has_evidence:
                diagnostic_diff += 1
                reasons.append(f"{assessment.condition_id} 诊断条件与未知用户状态分化")
                spans.extend(assessment.span_ids)
            continue
        if not has_evidence:
            continue
        if assessment.status == user_status:
            if core:
                reasons.append(f"{assessment.condition_id} 核心条件一致，尚不足以单独定角色")
            else:
                reasons.append(f"{assessment.condition_id} 诊断条件一致，不作支持")
            spans.extend(assessment.span_ids)
        elif diagnostic and not core:
            diagnostic_diff += 1
            reasons.append(f"{assessment.condition_id} 诊断条件分化")
            spans.extend(assessment.span_ids)
        elif core:
            reasons.append(f"{assessment.condition_id} 核心成员状态不同，须按条件组判断")
            spans.extend(assessment.span_ids)
    # 单项比较只保留证据说明，角色的决定性差异由同一套条件组语义计算。
    comparison = comparison or _compare_conditions(case, known, catalog, rule_ids)
    core_agree, core_conflict = comparison.core_agree, comparison.core_conflict
    if core_agree and core_conflict and (rule_linked or claim_aligned) and spans:
        # 角色是针对当前用户/候选规则的比较结果，不是案例永久的“好案/坏案”标签。
        # 此处按顺序判定：核心既一致又冲突 → 严格支持 → 核心限制 → 诊断边界 → 未定。
        return "boundary", reasons + ["核心条件组同时存在一致与冲突"], spans, None
    if (
        rule_linked
        and claim_aligned
        and core_agree
        and not core_conflict
        and applied
        and not rejected
        and spans
    ):
        return "support", reasons + ["目标规则、请求与决定性条件同时对齐"], spans, None
    if core_conflict and (rule_linked or claim_aligned) and spans:
        return "limiting", reasons + ["决定性核心条件相反，限制当前解释"], spans, None
    if diagnostic_diff and (rule_linked or claim_aligned):
        return "boundary", reasons, spans, None
    return (
        None,
        reasons,
        spans,
        _undetermined_reason(
            has_assessments=True,
            has_usable_evidence=has_usable_evidence,
            rule_linked=rule_linked,
            claim_aligned=claim_aligned,
        ),
    )


class HybridCypherCaseRetriever:
    """模块 C：混合案例召回＋候选规则/条件驱动的独立图召回，取并集后重排。"""

    def __init__(
        self,
        graph: LegalGraphGateway | GraphNavigator,
        search: HybridCandidateSearch,
        settings: HybridCypherSettings | None = None,
        *,
        catalog: RetrievalCatalog,
        navigator: GraphNavigator | None = None,
    ) -> None:
        self.graph = graph
        self.search = search
        self.settings = settings or HybridCypherSettings()
        self.catalog = catalog
        self.navigator = navigator or (
            graph if isinstance(graph, GraphNavigator) else MemoryGraphNavigator(catalog)
        )

    def retrieve(
        self,
        state: QueryState,
        rule_refs: list[ScoredReference],
    ) -> RetrievalBundle:
        # 【C 阅读顺序】文本/向量与三种图路径独立取候选 → 并集融合 → 条件相容性重排。
        # 最后才分配支持/限制/边界角色；分数高不代表一定有资格进入公共角色列表。
        context = require_context()
        if context.release_id != self.catalog.release_id:
            raise RetrieverUnavailable("案例索引与本轮图发布不一致")
        # 把 A 阶段找到的规则 ID 拿出来。
        rule_ids = [item.object_id for item in rule_refs if item.object_id in self.catalog.rules]
        if not rule_ids:
            raise RetrieverUnavailable("没有可检索的候选规则，拒绝空成功")
        query = _query_text(state)
        try:
            # 然后先做文本/向量案例搜索：
            hybrid = self.search.search(
                query,
                target="case",
                context=context,
                top_k=self.settings.case_recall_top_k,
            )
        except Exception as exc:
            raise RetrieverUnavailable("案例全文通道不可用") from exc
        condition_ids: list[str] = []
        for rule_id in rule_ids:
            condition_ids.extend(self.catalog.rules[rule_id].condition_ids)
        condition_ids = list(dict.fromkeys(condition_ids))
        graph_hits = self.navigator.cases_by_conditions(context, condition_ids)
        # 找同一条件的案例认定；下两路分别找裁判适用关系和相关法条引用。
        # 这些图候选不要求先被 BM25 命中，因此能补回词面不相似的案例。
        apply_hits = self.navigator.cases_applying_rules(context, rule_ids)
        cite_hits = self.navigator.cases_citing_provisions(context, rule_ids)
        graph_ids = list(dict.fromkeys(item.case_id for item in graph_hits))
        if len(graph_ids) > self.settings.per_condition_limit * max(1, len(condition_ids)):
            graph_ids = graph_ids[: self.settings.per_condition_limit * len(condition_ids)]
        cite_ids = list(dict.fromkeys(item.case_id for item in cite_hits))
        apply_ids = list(dict.fromkeys(item.case_id for item in apply_hits))
        ranked = {
            "bm25": [item.object_id for item in hybrid if "vector" not in item.retrieval_channels],
            "vector": [item.object_id for item in hybrid if "vector" in item.retrieval_channels],
            "graph": graph_ids,
            "applies": apply_ids,
            "cites": cite_ids,
        }
        fused = weighted_rrf(
            ranked,
            weights={
                "bm25": self.settings.fulltext_weight, # 1.0
                "graph": self.settings.graph_weight, # 1.0
                "applies": 0.6, # 辅助关联
                "vector": self.settings.vector_weight, # 1.0
                "cites": self.settings.cites_weight, # 辅助关联 0.5
            },
            k=self.settings.rrf_k,
        )

        known = _known_states(state)
        user_claims = {item.claim_type for item in state.candidate_claims}
        assessments_by_case: dict[str, list[CaseAssessment]] = defaultdict(list)
        paths: dict[str, list[GraphHit]] = defaultdict(list)
        for hit in [*graph_hits, *apply_hits, *cite_hits]:
            paths[hit.case_id].append(hit)
            if hit.condition_id and hit.status:
                assessments_by_case[hit.case_id].append(
                    CaseAssessment(
                        case_id=hit.case_id,
                        condition_id=hit.condition_id,
                        status=ConditionStatus(hit.status),
                        finding_ids=hit.finding_ids,
                        span_ids=hit.span_ids,
                    )
                )
        scored_cases: list[tuple[float, str, list[str], list[ScoreComponent]]] = []
        comparisons = {}
        roles_by_case = {}
        raw_case_scores = _raw_scores(hybrid)
        for case_id, rrf_score in fused.items():
            case = self.catalog.cases.get(case_id)
            if case is None:
                continue
            comparison = _compare_conditions(case, known, self.catalog, set(rule_ids))
            comparisons[case_id] = comparison
            # 在截断前判断所有已召回候选的角色；不是仅因条件不同就认定限制案例。
            roles_by_case[case_id] = _assign_role(
                case, known, set(rule_ids), self.catalog, user_claims, comparison,
            )
            final = rrf_score
            reasons = [f"条件一致：{list(comparison.same)}",
                       f"条件差异：{list(comparison.different)}",
                       f"未知或证据不足：{list(comparison.unknown)}"]
            # 公共 score 保留检索相关性，不再混入正负相容性；角色内部按覆盖度重排。
            components = [
                ScoreComponent(name="rrf", value=rrf_score, explanation="混合与图通道RRF"),
                ScoreComponent(name="comparison_coverage", value=comparison.coverage,
                               explanation="明确且有证据的共同条件占比，非概率"),
                ScoreComponent(name="same_conditions", value=len(comparison.same),
                               explanation="明确一致的条件数"),
                ScoreComponent(name="different_conditions", value=len(comparison.different),
                               explanation="明确不同的条件数，不作为负分"),
            ]
            for channel in ("bm25", "vector"):
                if (case_id, channel) in raw_case_scores:
                    components.append(
                        ScoreComponent(
                            name=channel,
                            value=raw_case_scores[(case_id, channel)],
                            explanation="召回通道原始分数",
                        )
                    )
            scored_cases.append((final, case_id, reasons, components))
        scored_cases.sort(key=lambda item: (-item[0], item[1]))
        # 各角色独立排序，再轮流保留，防止相近案例占满整个20件池。
        # 不推断“差异导致判决”：这里只复用原有角色资格，不增加因果证据声明。
        groups = {role: [] for role in ("support", "limiting", "boundary")}
        for row in scored_cases:
            role = roles_by_case[row[1]][0]
            if role is not None:
                groups[role].append(row)
        for rows in groups.values():
            rows.sort(key=lambda row: (-comparisons[row[1]].coverage, -row[0], row[1]))
        pool = []
        for index in range(max((len(rows) for rows in groups.values()), default=0)):
            for rows in groups.values():
                if index < len(rows) and len(pool) < self.settings.case_rerank_pool_size:
                    pool.append(rows[index])
        selected_ids = {row[1] for row in pool}
        # 有剩余容量才保留未定候选供追踪，不能挤掉已具备比较角色的案例。
        for row in scored_cases:
            if len(pool) >= self.settings.case_rerank_pool_size:
                break
            if row[1] not in selected_ids:
                pool.append(row)
                selected_ids.add(row[1])
        support: list[ScoredReference] = []
        limiting: list[ScoredReference] = []
        boundary: list[ScoredReference] = []
        cited: list[str] = []
        used: set[str] = set()
        undetermined: set[str] = set()
        undetermined_reasons: dict[str, str] = {}
        channels_by_case: dict[str, tuple[str, ...]] = {}
        import time

        from casepath.adapters.diagnostics.stage_events import emit_stage_event

        started = time.perf_counter()
        succeeded = False
        try:
            for score, case_id, reasons, components in pool:
                if case_id in used or case_id in undetermined:
                    continue
                case = self.catalog.cases[case_id]
                role, role_reasons, spans, undetermined_code = roles_by_case[case_id]
                channels = [name for name, ids in ranked.items() if case_id in ids]
                channels_by_case[case_id] = tuple(channels)
                graph_paths = []
                for hit in paths.get(case_id, ()):
                    if _valid_path(hit.path_nodes, hit.path_edges):
                        graph_paths.append(
                            RetrievalPath(
                                node_ids=list(hit.path_nodes),
                                edge_types=list(hit.path_edges),
                                score=score,
                                source_span_ids=list(hit.span_ids),
                            )
                        )
                if role is None:
                    # 仍记录在本轮融合池/未定集合，不强行凑三类案例；公共输出暂不包含该引用。
                    undetermined.add(case_id)
                    undetermined_reasons[case_id] = undetermined_code or "INSUFFICIENT_EVIDENCE"
                    continue
                used.add(case_id)
                # 先通过角色资格判定，再限制每类数量；不把溢出的支持案例改标成其他角色。
                # 每类在池中的相对顺序按比较覆盖度、RRF及稳定ID确定。
                role_refs = {"support": support, "limiting": limiting, "boundary": boundary}[role]
                if len(role_refs) >= self.settings.case_role_top_k:
                    continue
                ref = scored_reference(
                    case_id,
                    score,
                    channels=channels,
                    components=components,
                    reasons=reasons + role_reasons,
                    span_ids=list(dict.fromkeys([*case.span_ids, *spans])),
                    paths=graph_paths,
                )
                cited.extend(ref.source_span_ids)
                role_refs.append(ref)
            fused_ids = tuple(case_id for _, case_id, _, _ in pool)
            set_round(
                # 把本轮路径和认定交给后续比较器复用，避免再查一套不一致的图结果。
                # 注意 D/E 使用公共角色候选，不等于对融合池的所有未定案例也进行了模拟。
                RoundEvidence(
                    context=context,
                    assessments_by_case={
                        key: tuple(value)
                        for key, value in assessments_by_case.items()
                        if key in fused_ids
                    },
                    paths_by_case={
                        key: tuple((hit.path_nodes, hit.path_edges, hit.span_ids) for hit in value)
                        for key, value in paths.items()
                        if key in fused_ids
                    },
                    graph_only_ids=frozenset(
                        set(graph_ids) - set(ranked["bm25"]) - set(ranked["vector"])
                    ),
                    cite_only_ids=frozenset(
                        set(cite_ids)
                        - set(ranked["bm25"])
                        - set(ranked["vector"])
                        - set(graph_ids)
                        - set(apply_ids)
                    ),
                    fused_case_ids=fused_ids,
                    undetermined_case_ids=frozenset(undetermined),
                    channels_by_case=channels_by_case,
                    undetermined_reasons=dict(undetermined_reasons),
                    compared_rule_ids=tuple(rule_ids),
                )
            )
            degraded = len(support) + len(limiting) + len(boundary) <= 1
            succeeded = True
            return RetrievalBundle(
                rule_refs=rule_refs,
                support_case_refs=support,
                limiting_case_refs=limiting,
                boundary_case_refs=boundary,
                cited_span_ids=list(dict.fromkeys(cited)),
                degraded=degraded,
                degradation_reason=(
                    "深度案例不足，角色分组仅用于工程路径验证。" if degraded else None
                ),
                fused_case_count=len(fused_ids),
                undetermined_case_count=len(undetermined),
                undetermined_case_ids=sorted(undetermined),
                undetermined_reason_summary=sorted(set(undetermined_reasons.values())),
            )
        finally:
            emit_stage_event(
                stage="C_case_retrieval",
                duration_ms=(time.perf_counter() - started) * 1000,
                counts={
                    "fused": len(pool),
                    "support": len(support),
                    "limiting": len(limiting),
                    "boundary": len(boundary),
                    "undetermined": len(undetermined),
                },
                reason_codes=sorted(set(undetermined_reasons.values())),
                identifiers={"undetermined_case_ids": sorted(undetermined)[:50]},
                succeeded=succeeded,
            )
