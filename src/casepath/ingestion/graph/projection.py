"""将规则层、案例层及其显式引用确定性投影成图，不推断新法律关系。"""

import hashlib
import json
from collections import Counter
from pathlib import Path

from casepath.contracts import (
    CaseIndexRecord,
    CaseRecord,
    LegalSourceRecord,
    ProvisionRecord,
    RuleRecord,
    SourceSpan,
)
from casepath.ingestion.cases.validation import validate_canonical_cases
from casepath.ingestion.laws.jsonl import read_jsonl, sha256_file
from casepath.rule_layer.validation import validate_canonical_dataset

LABELS = frozenset(
    {
        "LegalSource",
        "Provision",
        "LegalRule",
        "RuleCondition",
        "ConditionGroup",
        "RuleException",
        "LegalConsequence",
        "Case",
        "Claim",
        "CourtFinding",
        "ConditionFinding",
        "ReasoningStep",
        "DecisionItem",
        "SourceSpan",
    }
)
RELATIONS = frozenset(
    {
        "BELONGS_TO",
        "BASED_ON",
        "HAS_CONDITION",
        "HAS_GROUP",
        "HAS_MEMBER",
        "HAS_EXCEPTION",
        "HAS_CONSEQUENCE",
        "EVIDENCED_BY",
        "CITES_PROVISION",
        "HAS_CLAIM",
        "HAS_FINDING",
        "HAS_ASSESSMENT",
        "HAS_REASONING",
        "HAS_DECISION",
        "INVOKES",
        "ABOUT",
        "APPLIES",
        "USES_FINDING",
        "DECIDES",
    }
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def node_key(label, identifier):
    # 类型参与标识，避免不同实体使用相同业务 ID 时互相覆盖。
    return canonical([label, identifier])


class GraphProjection:
    def __init__(self):
        self.nodes = {}
        self.edges = {}

    def node(self, label, identifier, properties):
        if label not in LABELS:
            raise ValueError(f"unknown graph label: {label}")
        key = node_key(label, identifier)
        value = {"key": key, "label": label, "object_id": identifier, "properties": properties}
        if key in self.nodes and self.nodes[key] != value:
            raise ValueError(f"conflicting shared node: {key}")
        self.nodes[key] = value
        return key

    def edge(self, source, relation, target):
        if relation not in RELATIONS:
            raise ValueError(f"unknown graph relationship: {relation}")
        value = {"source": source, "type": relation, "target": target}
        self.edges[digest(value)] = value

    def evidence(self, owner, spans):
        for identifier in spans:
            self.edge(owner, "EVIDENCED_BY", node_key("SourceSpan", identifier))

    def bundle(self, input_hashes):
        for edge in self.edges.values():
            if edge["source"] not in self.nodes or edge["target"] not in self.nodes:
                raise ValueError(f"dangling graph relationship: {edge}")
        payload = {
            "projection_version": "1.0",
            "input_hashes": input_hashes,
            "nodes": [self.nodes[k] for k in sorted(self.nodes)],
            "edges": [dict(key=k, **self.edges[k]) for k in sorted(self.edges)],
        }
        return dict(payload, release_id=digest(payload))


def props(model, exclude=()):
    return model.model_dump(mode="json", exclude=set(exclude))


def project_records(sources, provisions, rules, spans, lightweight, deep, input_hashes):
    graph = GraphProjection()
    for source in sources:
        graph.node("LegalSource", source.source_id, props(source))
    # 同一片段可能嵌入多条规则；内容相同才允许去重。
    all_spans = list(spans)
    for record in [*provisions, *rules, *deep]:
        all_spans.extend(record.source_spans)
    for span in all_spans:
        graph.node("SourceSpan", span.span_id, props(span))
    for provision in provisions:
        owner = graph.node("Provision", provision.provision_id, props(provision, ["source_spans"]))
        graph.edge(owner, "BELONGS_TO", node_key("LegalSource", provision.source_id))
        graph.evidence(owner, [s.span_id for s in provision.source_spans])
    for rule in rules:
        owner = graph.node(
            "LegalRule",
            rule.rule_id,
            props(
                rule,
                [
                    "provisions",
                    "conditions",
                    "condition_groups",
                    "exceptions",
                    "consequences",
                    "source_spans",
                ],
            ),
        )
        for ref in rule.provisions:
            graph.edge(owner, "BASED_ON", node_key("Provision", ref.provision_id))
        graph.evidence(owner, [s.span_id for s in rule.source_spans])
        for condition in rule.conditions:
            key = graph.node("RuleCondition", condition.condition_id, props(condition))
            graph.edge(owner, "HAS_CONDITION", key)
            graph.evidence(key, condition.source_span_ids)
        for group in rule.condition_groups:
            key = graph.node("ConditionGroup", group.group_id, props(group))
            graph.edge(owner, "HAS_GROUP", key)
            for member in group.member_condition_ids:
                graph.edge(key, "HAS_MEMBER", node_key("RuleCondition", member))
        for label, relation, field, records in [
            ("RuleException", "HAS_EXCEPTION", "exception_id", rule.exceptions),
            ("LegalConsequence", "HAS_CONSEQUENCE", "consequence_id", rule.consequences),
        ]:
            for record in records:
                key = graph.node(label, getattr(record, field), props(record))
                graph.edge(owner, relation, key)
                graph.evidence(key, record.source_span_ids)

    case_ids = set()
    for case in lightweight:
        if case.case_id in case_ids:
            raise ValueError("duplicate lightweight case ID")
        case_ids.add(case.case_id)
        owner = graph.node("Case", case.case_id, props(case))
        for ref in case.related_provision_ids:
            graph.edge(owner, "CITES_PROVISION", node_key("Provision", ref))
    deep_ids = set()
    for case in deep:
        if case.case_id in deep_ids or case.case_id not in case_ids:
            raise ValueError("duplicate deep case or missing lightweight case")
        deep_ids.add(case.case_id)
        owner = node_key("Case", case.case_id)
        # 轻量与深度合同版本分别保留，不能互相覆盖。
        graph.nodes[owner]["properties"].update(
            deep_contract_version=case.contract_version,
            deep_maturity=case.maturity.value,
        )
        for label, relation, field, records in [
            ("Claim", "HAS_CLAIM", "claim_id", case.claims),
            ("CourtFinding", "HAS_FINDING", "finding_id", case.findings),
            ("ReasoningStep", "HAS_REASONING", "reasoning_id", case.reasoning_steps),
            ("DecisionItem", "HAS_DECISION", "decision_id", case.decisions),
        ]:
            for record in records:
                key = graph.node(label, getattr(record, field), props(record))
                graph.edge(owner, relation, key)
                graph.evidence(key, getattr(record, "source_span_ids", []))
                for attr, edge_type, target_label in [
                    ("invoked_rule_ids", "INVOKES", "LegalRule"),
                    ("applied_rule_ids", "APPLIES", "LegalRule"),
                    ("premise_finding_ids", "USES_FINDING", "CourtFinding"),
                ]:
                    for ref in getattr(record, attr, []):
                        graph.edge(key, edge_type, node_key(target_label, ref))
                if label == "DecisionItem":
                    graph.edge(key, "DECIDES", node_key("Claim", record.claim_id))
        for assessment in case.condition_findings:
            identifier = "assessment." + digest([case.case_id, assessment.condition_id])
            key = graph.node(
                "ConditionFinding", identifier, dict(props(assessment), case_id=case.case_id)
            )
            graph.edge(owner, "HAS_ASSESSMENT", key)
            graph.edge(key, "ABOUT", node_key("RuleCondition", assessment.condition_id))
            for ref in assessment.finding_ids:
                graph.edge(key, "BASED_ON", node_key("CourtFinding", ref))
            graph.evidence(key, assessment.source_span_ids)
        graph.evidence(owner, [s.span_id for s in case.source_spans])
    return graph.bundle(input_hashes)


def load_projection(data_root: Path):
    # 发布校验先行；前后哈希一致才接受，避免读取过程中出现混合版本。
    files = sorted(
        [
            *data_root.glob("canonical/rules/*.jsonl"),
            *data_root.glob("canonical/cases/*.jsonl"),
            data_root / "manifests/civil_code.manifest.json",
            data_root / "manifests/cases.manifest.json",
        ]
    )
    before = {p.relative_to(data_root).as_posix(): sha256_file(p) for p in files}
    validate_canonical_dataset(data_root)
    validate_canonical_cases(data_root)
    models = [
        ("rules/legal_sources.jsonl", LegalSourceRecord),
        ("rules/provisions.jsonl", ProvisionRecord),
        ("rules/rules.jsonl", RuleRecord),
        ("rules/source_spans.jsonl", SourceSpan),
        ("cases/cases_l1.jsonl", CaseIndexRecord),
        ("cases/cases_deep.jsonl", CaseRecord),
    ]
    records = [read_jsonl(data_root / "canonical" / path, model) for path, model in models]
    if before != {p.relative_to(data_root).as_posix(): sha256_file(p) for p in files}:
        raise ValueError("canonical files changed during graph projection")
    return project_records(*records, input_hashes=before)


def report(bundle):
    return {
        "release_id": bundle["release_id"],
        "nodes": len(bundle["nodes"]),
        "edges": len(bundle["edges"]),
        "node_counts": dict(sorted(Counter(n["label"] for n in bundle["nodes"]).items())),
        "edge_counts": dict(sorted(Counter(e["type"] for e in bundle["edges"]).items())),
    }
