"""P4 实现区：有引用的条件化解释。未核验原文不得标成 verified。"""

from __future__ import annotations

from casepath.adapters.retrieval_catalog import RetrievalCatalog
from casepath.contracts import (
    CitationRecord,
    ComparisonBundle,
    ConditionStatus,
    EvidenceAction,
    ExplanationBranch,
    ExplanationPlan,
    QueryState,
    RetrievalBundle,
)


class EvidenceExplanationPlanner:
    def __init__(self, catalog: RetrievalCatalog) -> None:
        self.catalog = catalog

    def build(
        self,
        state: QueryState,
        bundle: RetrievalBundle,
        comparison: ComparisonBundle,
    ) -> ExplanationPlan:
        statuses = {item.condition_id: item.status for item in state.condition_states}
        unresolved = [
            key
            for key, value in statuses.items()
            if value in {ConditionStatus.UNKNOWN, ConditionStatus.CONFLICTING}
        ]
        mature = [
            self.catalog.rules[item.object_id].maturity
            for item in bundle.rule_refs
            if item.object_id in self.catalog.rules
        ]
        warning = ""
        if any(item in {"L1", "L2"} for item in mature):
            warning = "所用规则含尚未深度复核的条目，解释带成熟度警告。"
        if comparison.degraded and comparison.degradation_reason:
            warning = f"{warning}{comparison.degradation_reason}"
        impossible = statuses.get("cond.performance_impossible")
        if impossible is ConditionStatus.SATISFIED:
            main = "当前事实更支持分析服务合同解除及未履行部分的补救，但仍取决于其余核心条件。"
        elif impossible is ConditionStatus.NOT_SATISFIED:
            main = "若经营者仍能继续履行，合同目的不能实现的解除路径不能仅凭该事实成立。"
        else:
            main = "当前仍需确认关键履行事实后，才能在解除、催告迟延或继续履行之间收窄解释。"
        if warning:
            main = f"{main}{warning}"
        citations = []
        branches = []
        for ref in bundle.rule_refs:
            rule = self.catalog.rules.get(ref.object_id)
            if rule is None or not rule.span_ids:
                continue
            citation_id = f"citation.{ref.object_id}"
            citations.append(
                CitationRecord(
                    citation_id=citation_id,
                    source_span_ids=list(rule.span_ids[:2]),
                    supports=rule.title,
                    verified=False,
                )
            )
            branches.append(
                ExplanationBranch(
                    branch_id=f"branch.{ref.object_id}",
                    condition=f"若规则 {rule.title} 的核心条件成立",
                    explanation=rule.title + " 提供一条候选解释路径，不是裁判结论。",
                    citation_ids=[citation_id],
                )
            )
        for ref in (*bundle.support_case_refs, *bundle.limiting_case_refs, *bundle.boundary_case_refs):
            if not ref.source_span_ids:
                continue
            citation_id = f"citation.{ref.object_id}"
            citations.append(
                CitationRecord(
                    citation_id=citation_id,
                    source_span_ids=list(ref.source_span_ids[:2]),
                    supports="案例条件认定与原文",
                    verified=False,
                )
            )
        seen = set()
        unique_citations = []
        for item in citations:
            if item.citation_id in seen:
                continue
            seen.add(item.citation_id)
            unique_citations.append(item)
        actions = [
            EvidenceAction(
                action_id=f"evidence.{condition_id}",
                description=self.catalog.conditions[condition_id].predicate
                if condition_id in self.catalog.conditions
                else "补充可核验的事实材料。",
                related_condition_ids=[condition_id],
            )
            for condition_id in unresolved[:4]
        ]
        return ExplanationPlan(
            session_id=state.session_id,
            main_explanation=main,
            candidate_claims=[claim.claim_type for claim in state.candidate_claims],
            applicable_rule_ids=[item.object_id for item in bundle.rule_refs],
            support_case_ids=[item.object_id for item in bundle.support_case_refs],
            limiting_case_ids=[item.object_id for item in bundle.limiting_case_refs],
            boundary_case_ids=[item.object_id for item in bundle.boundary_case_refs],
            conditional_branches=branches[:3],
            unresolved_condition_ids=unresolved,
            evidence_actions=actions,
            citations=unique_citations,
        )
