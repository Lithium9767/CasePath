"""P4 实现区：HybridCypher 规则/案例检索。

混合召回＋固定图扩展，并保留规则条件驱动的独立图召回。不提供四选一选择器。
请求级 RetrievalContext 用 contextvars 传递，禁止写入共享 retriever 字段。
图路径只返回投影中真实存在的中间节点和有向边；全文命中不编造路径。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal, Protocol

from casepath.adapters.condition_logic import group_truth
from casepath.adapters.graph_navigator import GraphHit, GraphNavigator, MemoryGraphNavigator
from casepath.adapters.retrieval_catalog import (
    CaseAssessment,
    CaseDocument,
    RetrievalCatalog,
    RetrievalContext,
    RoundEvidence,
    bind_context,
    bind_round,
    require_context,
    scored_reference,
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
    case_pool_size: int = 100
    per_condition_limit: int = 30
    rrf_k: int = 60
    fulltext_weight: float = 1.0
    vector_weight: float = 1.0
    claim_weight: float = 0.8
    graph_weight: float = 1.0
    cites_weight: float = 0.5
    compatibility_weight: float = 0.35

    def __post_init__(self) -> None:
        numeric = [
            self.rule_top_k,
            self.case_pool_size,
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
    parts = [state.initial_query]
    parts.extend(item.text for item in state.user_facts)
    parts.extend(item.claim_type for item in state.candidate_claims)
    parts.extend(item.requested_remedy for item in state.candidate_claims)
    return "\n".join(part for part in parts if part)


def _claim_rank(state: QueryState, catalog: RetrievalCatalog) -> list[str]:
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
        context = self.catalog.context()
        bind_context(context)
        if context.release_id != self.catalog.release_id:
            raise RetrieverUnavailable("规则索引与本轮图发布不一致")
        query = _query_text(state)
        if not query.strip():
            raise RetrieverUnavailable("查询文本为空，拒绝返回空成功")
        try:
            fulltext = self.search.search(
                query,
                target="rule",
                context=context,
                top_k=min(self.settings.rule_top_k, max(1, len(self.catalog.rules))),
            )
        except Exception as exc:
            raise RetrieverUnavailable("规则全文通道不可用") from exc
        claim_ids = _claim_rank(state, self.catalog)
        seed_ids = list(dict.fromkeys([item.object_id for item in fulltext] + claim_ids))
        neighbors = self.navigator.neighbor_rules(context, seed_ids)
        graph_ids = list(dict.fromkeys(item.rule_id for item in neighbors))
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
                "bm25": self.settings.fulltext_weight,
                "vector": self.settings.vector_weight,
                "claim": self.settings.claim_weight,
                # 邻接通道没有查询相关度，不让按 ID 排序的首位与直接命中平权。
                "graph": 0.25 * self.settings.graph_weight,
            },
            k=self.settings.rrf_k,
        )
        raw = _raw_scores(fulltext)
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
        return results[:limit]


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


def _compatibility(
    case: CaseDocument,
    known: dict[str, ConditionStatus],
    catalog: RetrievalCatalog,
    rule_ids: set[str],
) -> tuple[float, list[str]]:
    """UNKNOWN / NOT_APPLICABLE 不按否定过滤；DIAGNOSTIC 不满足不排除核心规则关联案例。"""

    agree, conflict = _core_group_comparison(case, known, catalog, rule_ids)
    score = float(agree - conflict)
    reasons: list[str] = []
    by_condition = {item.condition_id: item for item in case.assessments}
    for condition_id, status in known.items():
        if status not in _DECISIVE:
            continue
        assessment = by_condition.get(condition_id)
        if assessment is None or assessment.status not in _DECISIVE:
            continue
        roles = _condition_roles(catalog, condition_id, rule_ids)
        diagnostic_only = roles and all(role == "DIAGNOSTIC" for role in roles)
        if assessment.status == status and diagnostic_only:
            score += 0.15
            reasons.append(f"{condition_id} 诊断条件与用户状态一致")
        elif diagnostic_only:
            reasons.append(f"{condition_id} 为 DIAGNOSTIC，不一致不排除")
    reasons.append(f"CORE 条件组：满足一致 {agree}，明确冲突 {conflict}")
    return score, reasons


def _core_group_comparison(
    case: CaseDocument,
    known: dict[str, ConditionStatus],
    catalog: RetrievalCatalog,
    rule_ids: set[str],
) -> tuple[int, int]:
    """先按组求值再比较，不将 ANY 中非决定性的单项差异计为冲突。"""
    case_states: dict[str, ConditionStatus] = {}
    for item in case.assessments:
        if not (item.span_ids or item.finding_ids):
            continue
        previous = case_states.get(item.condition_id)
        case_states[item.condition_id] = (
            ConditionStatus.CONFLICTING
            if previous is not None and previous != item.status
            else item.status
        )
    agree = conflict = 0
    for rule_id in sorted(rule_ids):
        rule = catalog.rules.get(rule_id)
        if rule is None:
            continue
        for group in rule.groups:
            if group.role != "CORE":
                continue
            user_value = group_truth(
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


def _assign_role(
    case: CaseDocument,
    known: dict[str, ConditionStatus],
    rule_ids: set[str],
    catalog: RetrievalCatalog,
    user_claims: set[str],
) -> tuple[str | None, list[str], list[str]]:
    """支持/限制必须结合目标规则、请求、决定性条件差异和理由依据。

    未知、不适用不得当成明确相反条件。不能仅因图通道或单条件一致而标成支持。
    """

    if not case.assessments:
        return None, [], []
    case_claims = {item.claim_type for item in case.claims}
    if user_claims and case_claims:
        claim_aligned = bool(user_claims & case_claims)
    else:
        claim_aligned = not user_claims
    applied = set(case.applied_rule_ids) & rule_ids
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
    for assessment in case.assessments:
        user_status = known.get(assessment.condition_id, ConditionStatus.UNKNOWN)
        has_evidence = bool(assessment.span_ids or assessment.finding_ids)
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
    core_agree, core_conflict = _core_group_comparison(case, known, catalog, rule_ids)
    if core_agree and core_conflict and (rule_linked or claim_aligned) and spans:
        return "boundary", reasons + ["核心条件组同时存在一致与冲突"], spans
    if (
        rule_linked
        and claim_aligned
        and core_agree
        and not core_conflict
        and applied
        and not rejected
        and spans
    ):
        return "support", reasons + ["目标规则、请求与决定性条件同时对齐"], spans
    if core_conflict and (rule_linked or claim_aligned) and spans:
        return "limiting", reasons + ["决定性核心条件相反，限制当前解释"], spans
    if diagnostic_diff and (rule_linked or claim_aligned):
        return "boundary", reasons, spans
    return None, reasons, spans


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
        context = require_context()
        if context.release_id != self.catalog.release_id:
            raise RetrieverUnavailable("案例索引与本轮图发布不一致")
        rule_ids = [item.object_id for item in rule_refs if item.object_id in self.catalog.rules]
        if not rule_ids:
            raise RetrieverUnavailable("没有可检索的候选规则，拒绝空成功")
        query = _query_text(state)
        try:
            hybrid = self.search.search(
                query,
                target="case",
                context=context,
                top_k=self.settings.case_pool_size,
            )
        except Exception as exc:
            raise RetrieverUnavailable("案例全文通道不可用") from exc
        condition_ids: list[str] = []
        for rule_id in rule_ids:
            condition_ids.extend(self.catalog.rules[rule_id].condition_ids)
        condition_ids = list(dict.fromkeys(condition_ids))
        graph_hits = self.navigator.cases_by_conditions(context, condition_ids)
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
                "bm25": self.settings.fulltext_weight,
                "graph": self.settings.graph_weight,
                "applies": 0.6,
                "vector": self.settings.vector_weight,
                "cites": self.settings.cites_weight,
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
        raw_case_scores = _raw_scores(hybrid)
        for case_id, rrf_score in fused.items():
            case = self.catalog.cases.get(case_id)
            if case is None:
                continue
            compat, reasons = _compatibility(case, known, self.catalog, set(rule_ids))
            final = rrf_score + self.settings.compatibility_weight * compat
            components = [
                ScoreComponent(name="rrf", value=rrf_score, explanation="混合与图通道RRF"),
                ScoreComponent(name="compatibility", value=compat, explanation="已知条件相容性"),
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
        pool = scored_cases[: self.settings.case_pool_size]
        support: list[ScoredReference] = []
        limiting: list[ScoredReference] = []
        boundary: list[ScoredReference] = []
        cited: list[str] = []
        used: set[str] = set()
        undetermined: set[str] = set()
        channels_by_case: dict[str, tuple[str, ...]] = {}
        for score, case_id, reasons, components in pool:
            if case_id in used or case_id in undetermined:
                continue
            case = self.catalog.cases[case_id]
            role, role_reasons, spans = _assign_role(
                case, known, set(rule_ids), self.catalog, user_claims
            )
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
                undetermined.add(case_id)
                continue
            used.add(case_id)
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
            if role == "support":
                support.append(ref)
            elif role == "limiting":
                limiting.append(ref)
            else:
                boundary.append(ref)
        fused_ids = tuple(case_id for _, case_id, _, _ in pool)
        bind_round(
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
            )
        )
        degraded = len(support) + len(limiting) + len(boundary) <= 1
        return RetrievalBundle(
            rule_refs=rule_refs,
            support_case_refs=support,
            limiting_case_refs=limiting,
            boundary_case_refs=boundary,
            cited_span_ids=list(dict.fromkeys(cited)),
            degraded=degraded,
            degradation_reason="深度案例不足，角色分组仅用于工程路径验证。" if degraded else None,
        )
