"""P4 实现区：固定类型图扩展。内存邻接与 Neo4j Cypher 走同一组路径。

只返回投影中真实存在的中间节点和有向边。不存在 INSTANTIATES/JUSTIFIES/REALIZES。
没有 finding 的桥接只回原文，不补造认定。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from casepath.adapters.retrieval.retrieval_catalog import (
    CaseAssessment,
    RetrievalCatalog,
    RetrievalContext,
)
from casepath.application.errors import GraphUnavailable
from casepath.ports import LegalGraphGateway

RULE_NEIGHBORS = """
MATCH (rule:LegalRule {release_id:$release_id})-[:HAS_GROUP]->(:ConditionGroup)
      -[:HAS_MEMBER]->(c:RuleCondition)
WHERE rule.object_id IN $rule_ids
MATCH (other:LegalRule {release_id:$release_id})-[:HAS_GROUP]->(og:ConditionGroup)
      -[:HAS_MEMBER]->(c)
WHERE NOT other.object_id IN $rule_ids
RETURN DISTINCT other.object_id AS rule_id, c.object_id AS condition_id,
       og.object_id AS group_id
"""

CASES_BY_CONDITION = """
MATCH (c:RuleCondition {release_id:$release_id})<-[:ABOUT]-(a:ConditionFinding)
      <-[:HAS_ASSESSMENT]-(case:Case {release_id:$release_id})
WHERE c.object_id IN $condition_ids
OPTIONAL MATCH (a)-[:BASED_ON]->(f:CourtFinding)
OPTIONAL MATCH (a)-[:EVIDENCED_BY]->(s:SourceSpan)
RETURN case.object_id AS case_id, c.object_id AS condition_id,
       a.object_id AS assessment_id, a.status AS status,
       collect(DISTINCT f.object_id) AS finding_ids,
       collect(DISTINCT s.object_id) AS span_ids
"""

CASES_APPLYING_RULE = """
MATCH (case:Case {release_id:$release_id})-[:HAS_REASONING]->(step:ReasoningStep)
      -[:APPLIES]->(rule:LegalRule {release_id:$release_id})
WHERE rule.object_id IN $rule_ids
RETURN DISTINCT case.object_id AS case_id, rule.object_id AS rule_id,
       step.object_id AS reasoning_id
"""

CASES_CITING_PROVISION = """
MATCH (rule:LegalRule {release_id:$release_id})-[:BASED_ON]->(p:Provision)
      <-[:CITES_PROVISION]-(case:Case {release_id:$release_id})
WHERE rule.object_id IN $rule_ids
RETURN DISTINCT case.object_id AS case_id, p.object_id AS provision_id,
       rule.object_id AS rule_id
"""


@dataclass(frozen=True)
class GraphHit:
    case_id: str
    condition_id: str | None
    rule_id: str | None
    status: str | None
    finding_ids: tuple[str, ...]
    span_ids: tuple[str, ...]
    path_nodes: tuple[str, ...]
    path_edges: tuple[str, ...]


@dataclass(frozen=True)
class RuleGraphHit:
    rule_id: str
    condition_id: str
    group_id: str
    path_nodes: tuple[str, ...]
    path_edges: tuple[str, ...]


class GraphNavigator:
    def neighbor_rules(self, context: RetrievalContext, rule_ids: list[str]) -> list[RuleGraphHit]:
        raise NotImplementedError

    def cases_by_conditions(
        self, context: RetrievalContext, condition_ids: list[str]
    ) -> list[GraphHit]:
        raise NotImplementedError

    def cases_applying_rules(
        self, context: RetrievalContext, rule_ids: list[str]
    ) -> list[GraphHit]:
        raise NotImplementedError

    def cases_citing_provisions(
        self, context: RetrievalContext, rule_ids: list[str]
    ) -> list[GraphHit]:
        raise NotImplementedError


class MemoryGraphNavigator(GraphNavigator):
    """与投影同一 release 的邻接表；只回答本轮 context.release_id。"""

    def __init__(self, catalog: RetrievalCatalog) -> None:
        self.catalog = catalog
        self._condition_cases: dict[str, list[CaseAssessment]] = defaultdict(list)
        for case in catalog.cases.values():
            for item in case.assessments:
                self._condition_cases[item.condition_id].append(item)

    def _guard(self, context: RetrievalContext) -> None:
        if context.release_id != self.catalog.release_id:
            raise GraphUnavailable("图邻接不属于本轮固定发布")

    def neighbor_rules(self, context: RetrievalContext, rule_ids: list[str]) -> list[RuleGraphHit]:
        self._guard(context)
        seeds = set(rule_ids)
        found: list[RuleGraphHit] = []
        for condition_id, owners in self.catalog.rule_ids_by_condition.items():
            if not seeds & set(owners):
                continue
            for owner in owners:
                if owner in seeds:
                    continue
                rule = self.catalog.rules.get(owner)
                if rule is None:
                    continue
                group_id = next(
                    (group.group_id for group in rule.groups if condition_id in group.member_ids),
                    "",
                )
                if not group_id:
                    continue
                found.append(
                    RuleGraphHit(
                        rule_id=owner,
                        condition_id=condition_id,
                        group_id=group_id,
                        path_nodes=(owner, group_id, condition_id),
                        path_edges=("HAS_GROUP", "HAS_MEMBER"),
                    )
                )
        return sorted(found, key=lambda item: (item.rule_id, item.condition_id, item.group_id))

    def cases_by_conditions(
        self, context: RetrievalContext, condition_ids: list[str]
    ) -> list[GraphHit]:
        self._guard(context)
        hits: list[GraphHit] = []
        for condition_id in condition_ids:
            for item in self._condition_cases.get(condition_id, ()):
                assessment_id = item.assessment_id or f"assessment.{item.case_id}.{condition_id}"
                hits.append(
                    GraphHit(
                        case_id=item.case_id,
                        condition_id=condition_id,
                        rule_id=None,
                        status=item.status.value,
                        finding_ids=item.finding_ids,
                        span_ids=item.span_ids,
                        path_nodes=(item.case_id, assessment_id, condition_id),
                        path_edges=("HAS_ASSESSMENT", "ABOUT"),
                    )
                )
        return hits

    def cases_applying_rules(
        self, context: RetrievalContext, rule_ids: list[str]
    ) -> list[GraphHit]:
        self._guard(context)
        hits: list[GraphHit] = []
        wanted = set(rule_ids)
        for case in self.catalog.cases.values():
            for step in case.reasoning:
                for rule_id in step.applied_rule_ids:
                    if rule_id not in wanted:
                        continue
                    hits.append(
                        GraphHit(
                            case_id=case.case_id,
                            condition_id=None,
                            rule_id=rule_id,
                            status=None,
                            finding_ids=(),
                            span_ids=step.span_ids,
                            path_nodes=(case.case_id, step.reasoning_id, rule_id),
                            path_edges=("HAS_REASONING", "APPLIES"),
                        )
                    )
        return hits

    def cases_citing_provisions(
        self, context: RetrievalContext, rule_ids: list[str]
    ) -> list[GraphHit]:
        self._guard(context)
        hits: list[GraphHit] = []
        provision_to_rules: dict[str, set[str]] = defaultdict(set)
        for rule_id in rule_ids:
            rule = self.catalog.rules.get(rule_id)
            if rule is None:
                continue
            for provision_id in rule.provision_ids:
                provision_to_rules[provision_id].add(rule_id)
        for case in self.catalog.cases.values():
            for provision_id in case.cited_provision_ids:
                if provision_id not in provision_to_rules:
                    continue
                hits.append(
                    GraphHit(
                        case_id=case.case_id,
                        condition_id=None,
                        rule_id=min(provision_to_rules[provision_id]),
                        status=None,
                        finding_ids=(),
                        span_ids=(),
                        path_nodes=(case.case_id, provision_id),
                        path_edges=("CITES_PROVISION",),
                    )
                )
        return hits


class Neo4jGraphNavigator(GraphNavigator):
    """只读 Cypher；失败向上抛出，不改写成空成功。"""

    def __init__(self, gateway: LegalGraphGateway) -> None:
        self.gateway = gateway

    def _rows(self, query: str, context: RetrievalContext, **parameters: object) -> list[dict]:
        try:
            rows = self.gateway.execute_read(
                query, {"release_id": context.release_id, **parameters}
            )
        except GraphUnavailable:
            raise
        except Exception as exc:
            raise GraphUnavailable("法律图查询失败") from exc
        return [dict(row) for row in rows]

    def neighbor_rules(self, context: RetrievalContext, rule_ids: list[str]) -> list[RuleGraphHit]:
        if not rule_ids:
            return []
        hits = []
        for row in self._rows(RULE_NEIGHBORS, context, rule_ids=rule_ids):
            group_id = str(row["group_id"])
            rule_id = str(row["rule_id"])
            condition_id = str(row["condition_id"])
            hits.append(
                RuleGraphHit(
                    rule_id=rule_id,
                    condition_id=condition_id,
                    group_id=group_id,
                    path_nodes=(rule_id, group_id, condition_id),
                    path_edges=("HAS_GROUP", "HAS_MEMBER"),
                )
            )
        return sorted(hits, key=lambda item: (item.rule_id, item.condition_id, item.group_id))

    def cases_by_conditions(
        self, context: RetrievalContext, condition_ids: list[str]
    ) -> list[GraphHit]:
        if not condition_ids:
            return []
        hits = []
        for row in self._rows(CASES_BY_CONDITION, context, condition_ids=condition_ids):
            findings = tuple(item for item in row.get("finding_ids") or [] if item)
            spans = tuple(item for item in row.get("span_ids") or [] if item)
            assessment_id = str(row["assessment_id"])
            case_id = str(row["case_id"])
            condition_id = str(row["condition_id"])
            hits.append(
                GraphHit(
                    case_id=case_id,
                    condition_id=condition_id,
                    rule_id=None,
                    status=str(row["status"]) if row.get("status") else None,
                    finding_ids=findings,
                    span_ids=spans,
                    path_nodes=(case_id, assessment_id, condition_id),
                    path_edges=("HAS_ASSESSMENT", "ABOUT"),
                )
            )
        return hits

    def cases_applying_rules(
        self, context: RetrievalContext, rule_ids: list[str]
    ) -> list[GraphHit]:
        if not rule_ids:
            return []
        hits = []
        for row in self._rows(CASES_APPLYING_RULE, context, rule_ids=rule_ids):
            case_id = str(row["case_id"])
            rule_id = str(row["rule_id"])
            reasoning_id = str(row["reasoning_id"])
            hits.append(
                GraphHit(
                    case_id=case_id,
                    condition_id=None,
                    rule_id=rule_id,
                    status=None,
                    finding_ids=(),
                    span_ids=(),
                    path_nodes=(case_id, reasoning_id, rule_id),
                    path_edges=("HAS_REASONING", "APPLIES"),
                )
            )
        return hits

    def cases_citing_provisions(
        self, context: RetrievalContext, rule_ids: list[str]
    ) -> list[GraphHit]:
        if not rule_ids:
            return []
        hits = []
        for row in self._rows(CASES_CITING_PROVISION, context, rule_ids=rule_ids):
            case_id = str(row["case_id"])
            provision_id = str(row["provision_id"])
            hits.append(
                GraphHit(
                    case_id=case_id,
                    condition_id=None,
                    rule_id=str(row["rule_id"]) if row.get("rule_id") else None,
                    status=None,
                    finding_ids=(),
                    span_ids=(),
                    path_nodes=(case_id, provision_id),
                    path_edges=("CITES_PROVISION",),
                )
            )
        return hits
