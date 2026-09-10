"""P4 实现区：版本固定的检索目录、自建 BM25 与请求级上下文。

全文与向量必须索引同一组对象 ID。不把裁判结果、金额或支持/限制角色写入
案例初始召回文档。这里的 BM25 是词袋实现，不声称 BM25F。
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Literal

from casepath.contracts import (
    CaseIndexRecord,
    CaseRecord,
    ConditionGroupRole,
    ConditionStatus,
    DecisionStatus,
    ProvisionRecord,
    RuleRecord,
    ScoreComponent,
    ScoredReference,
)
from casepath.ingestion.graph.projection import digest
from casepath.ingestion.laws.jsonl import read_jsonl

_TOKEN = re.compile(r"[0-9A-Za-z_]+|[\u4e00-\u9fff]")
_CONTEXT: ContextVar[RetrievalContext | None] = ContextVar(
    "casepath_retrieval_context", default=None
)
_ROUND: ContextVar[RoundEvidence | None] = ContextVar("casepath_round_evidence", default=None)


@dataclass(frozen=True)
class RetrievalContext:
    """一轮固定图发布和索引版本；禁止写入共享 retriever 可变属性。"""

    release_id: str
    index_version: str
    embedding_model: str


@dataclass(frozen=True)
class ConditionCard:
    condition_id: str
    label: str
    predicate: str
    evidence_types: tuple[str, ...]
    span_ids: tuple[str, ...]
    user_answerable: bool = True


@dataclass(frozen=True)
class ConditionGroupSpec:
    group_id: str
    role: str
    operator: str
    member_ids: tuple[str, ...]


@dataclass(frozen=True)
class RuleDocument:
    rule_id: str
    title: str
    claim_types: tuple[str, ...]
    condition_ids: tuple[str, ...]
    core_ids: tuple[str, ...]
    diagnostic_ids: tuple[str, ...]
    groups: tuple[ConditionGroupSpec, ...]
    provision_ids: tuple[str, ...]
    span_ids: tuple[str, ...]
    search_text: str
    maturity: str


@dataclass(frozen=True)
class CaseAssessment:
    case_id: str
    condition_id: str
    status: ConditionStatus
    finding_ids: tuple[str, ...]
    span_ids: tuple[str, ...]
    assessment_id: str = ""


@dataclass(frozen=True)
class CaseClaimView:
    claim_id: str
    claim_type: str
    requested_remedy: str
    invoked_rule_ids: tuple[str, ...]


@dataclass(frozen=True)
class CaseDecisionView:
    decision_id: str
    claim_id: str
    status: DecisionStatus
    description: str


@dataclass(frozen=True)
class CaseReasoningView:
    reasoning_id: str
    applied_rule_ids: tuple[str, ...]
    conclusion: str
    span_ids: tuple[str, ...]


@dataclass(frozen=True)
class CaseDocument:
    case_id: str
    title: str
    search_text: str
    cited_provision_ids: tuple[str, ...]
    applied_rule_ids: tuple[str, ...]
    invoked_rule_ids: tuple[str, ...]
    assessments: tuple[CaseAssessment, ...]
    claims: tuple[CaseClaimView, ...]
    decisions: tuple[CaseDecisionView, ...]
    reasoning: tuple[CaseReasoningView, ...]
    span_ids: tuple[str, ...]
    finding_predicates: tuple[str, ...]
    finding_ids: tuple[str, ...]


@dataclass
class RoundEvidence:
    """本轮 C 写入、D/解释读取；不得跨请求复用。"""

    context: RetrievalContext
    assessments_by_case: dict[str, tuple[CaseAssessment, ...]]
    paths_by_case: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]]
    graph_only_ids: frozenset[str]
    cite_only_ids: frozenset[str] = frozenset()
    fused_case_ids: tuple[str, ...] = ()
    undetermined_case_ids: frozenset[str] = frozenset()
    channels_by_case: dict[str, tuple[str, ...]] | None = None


@dataclass
class RetrievalCatalog:
    """单一 release 的规则/案例检索视图。"""

    release_id: str
    index_version: str
    embedding_model: str
    rules: dict[str, RuleDocument]
    cases: dict[str, CaseDocument]
    conditions: dict[str, ConditionCard]
    rule_ids_by_condition: dict[str, tuple[str, ...]]
    provision_text: dict[str, str]
    # 解释侧证据，不参与检索文本或 index_version，避免无意义地重建 embedding。
    rule_records: dict[str, RuleRecord] = field(default_factory=dict)
    case_records: dict[str, CaseRecord] = field(default_factory=dict)

    def context(self) -> RetrievalContext:
        return RetrievalContext(self.release_id, self.index_version, self.embedding_model)


def tokenize(text: str) -> list[str]:
    parts = _TOKEN.findall(text.lower())
    tokens = list(parts)
    han = [p for p in parts if len(p) == 1 and "\u4e00" <= p <= "\u9fff"]
    tokens.extend(a + b for a, b in pairwise(han))
    return tokens


class BM25Index:
    """自建 BM25；k1/b 为工程默认值，需开发集校准后才能当论文超参。"""

    def __init__(self, documents: dict[str, str], *, k1: float = 1.2, b: float = 0.75) -> None:
        # 建立词项统计：少见词的 IDF 更高，长文通过长度归一化避免天然占优。
        # 此实现把文档分词结果留在内存，search 时遍历文档，并非 Neo4j 的全文索引接口。
        if k1 <= 0 or not 0 <= b <= 1:
            raise ValueError("BM25 parameters must be valid")
        self.k1 = k1
        self.b = b
        self.ids = sorted(documents)
        self.tokens = {oid: tokenize(documents[oid]) for oid in self.ids}
        self.n = len(self.ids)
        lengths = [len(self.tokens[oid]) or 1 for oid in self.ids]
        self.avgdl = sum(lengths) / len(lengths) if lengths else 1.0
        df: Counter[str] = Counter()
        for oid in self.ids:
            df.update(set(self.tokens[oid]))
        self.idf = {
            term: math.log((self.n - freq + 0.5) / (freq + 0.5) + 1.0) for term, freq in df.items()
        }

    def search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        q_terms = tokenize(query)
        if not q_terms or not self.ids:
            return []
        scored: list[tuple[str, float]] = []
        for oid in self.ids:
            tf = Counter(self.tokens[oid])
            dl = len(self.tokens[oid]) or 1
            score = 0.0
            for term in q_terms:
                if term not in tf:
                    continue
                freq = tf[term]
                denom = freq + self.k1 * (1.0 - self.b + self.b * dl / self.avgdl)
                # freq 是词频，dl 是本文长度，avgdl 是平均长度；k1 控制词频饱和，b 控制长度惩罚。
                score += self.idf.get(term, 0.0) * (freq * (self.k1 + 1.0) / denom)
            if score > 0:
                scored.append((oid, score))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:top_k]


def weighted_rrf(
    ranked: dict[str, list[str]],
    *,
    weights: dict[str, float],
    k: int,
) -> dict[str, float]:
    """缺席通道贡献 0；不把不同量纲原始分相加。"""

    if k <= 0:
        raise ValueError("RRF k must be positive")
    scores: dict[str, float] = defaultdict(float)
    # RRF(object) = Σ 通道权重/(k + 该通道名次)，名次从 1 开始；没出现的通道贡献 0。
    # 例如两个通道都排名靠前会累加得分；k 是平滑常数，不是最终返回的 top_k。
    for channel, ids in ranked.items():
        weight = weights.get(channel, 0.0)
        if weight == 0:
            continue
        seen: set[str] = set()
        for rank, oid in enumerate(ids, start=1):
            if oid in seen:
                continue
            seen.add(oid)
            scores[oid] += weight / (k + rank)
    return dict(scores)


def scored_reference(
    object_id: str,
    score: float,
    *,
    channels: list[str],
    components: list[ScoreComponent],
    reasons: list[str],
    span_ids: list[str] | None = None,
    paths: list | None = None,
) -> ScoredReference:
    return ScoredReference(
        object_id=object_id,
        score=score,
        reasons=reasons,
        score_components=components,
        retrieval_channels=sorted(set(channels)),
        source_span_ids=list(span_ids or []),
        graph_paths=list(paths or []),
    )


def _rule_search_text(rule: RuleRecord, provision_text: dict[str, str]) -> str:
    # 【c 索引文本】一规则一文本：标题、请求类型、条件、相关法条和原文引句。
    # BM25 与 embedding 共用 search_text；不是对图中每个节点做图嵌入。
    chunks = [rule.title, *rule.claim_types]
    for condition in rule.conditions:
        chunks.extend([condition.label, condition.predicate])
    for ref in rule.provisions:
        chunks.append(provision_text.get(ref.provision_id, ""))
        chunks.append(ref.title)
    for span in rule.source_spans:
        chunks.append(span.quote)
    return "\n".join(chunk for chunk in chunks if chunk)


def _case_search_text(light: CaseIndexRecord, deep: CaseRecord | None) -> str:
    # 一案一文本：标题/案由/关键词及提取的查明 predicate，并非整篇案例都参与向量计算。
    # 完整说理与裁判结果可以在解释证据包使用，和检索文本范围是两回事。
    # 轻量案例不用 facts_text/summary 全文：其中可能混有裁判主文和结果。
    parts = [light.title, light.cause or "", *light.keywords]
    if deep is not None:
        parts.extend(item.predicate for item in deep.findings)
    return "\n".join(part for part in parts if part)


def build_catalog(
    *,
    release_id: str,
    rules: Iterable[RuleRecord],
    provisions: Iterable[ProvisionRecord],
    lightweight: Iterable[CaseIndexRecord],
    deep: Iterable[CaseRecord],
    embedding_model: str = "none",
) -> RetrievalCatalog:
    rules = list(rules)
    provision_text = {item.provision_id: item.text for item in provisions}
    rule_docs: dict[str, RuleDocument] = {}
    conditions: dict[str, ConditionCard] = {}
    owners: dict[str, list[str]] = defaultdict(list)
    for rule in rules:
        core: list[str] = []
        diagnostic: list[str] = []
        groups: list[ConditionGroupSpec] = []
        for group in rule.condition_groups:
            target = core if group.role is ConditionGroupRole.CORE else diagnostic
            target.extend(group.member_condition_ids)
            groups.append(
                ConditionGroupSpec(
                    group_id=group.group_id,
                    role=group.role.value,
                    operator=group.operator.value,
                    member_ids=tuple(group.member_condition_ids),
                )
            )
        for condition in rule.conditions:
            conditions.setdefault(
                condition.condition_id,
                ConditionCard(
                    condition_id=condition.condition_id,
                    label=condition.label,
                    predicate=condition.predicate,
                    evidence_types=tuple(condition.evidence_types),
                    span_ids=tuple(condition.source_span_ids),
                    user_answerable=condition.user_answerable,
                ),
            )
            owners[condition.condition_id].append(rule.rule_id)
        rule_docs[rule.rule_id] = RuleDocument(
            rule_id=rule.rule_id,
            title=rule.title,
            claim_types=tuple(rule.claim_types),
            condition_ids=tuple(item.condition_id for item in rule.conditions),
            core_ids=tuple(dict.fromkeys(core)),
            diagnostic_ids=tuple(dict.fromkeys(diagnostic)),
            groups=tuple(groups),
            provision_ids=tuple(item.provision_id for item in rule.provisions),
            span_ids=tuple(item.span_id for item in rule.source_spans),
            search_text=_rule_search_text(rule, provision_text),
            maturity=rule.maturity.value,
        )
    deep_by_id = {item.case_id: item for item in deep}
    case_docs: dict[str, CaseDocument] = {}
    for light in lightweight:
        record = deep_by_id.get(light.case_id)
        assessments: list[CaseAssessment] = []
        applied: list[str] = []
        invoked: list[str] = []
        spans = []
        predicates: list[str] = []
        finding_ids: list[str] = []
        claims: list[CaseClaimView] = []
        decisions: list[CaseDecisionView] = []
        reasoning: list[CaseReasoningView] = []
        if record is not None:
            spans.extend(item.span_id for item in record.source_spans)
            predicates.extend(item.predicate for item in record.findings)
            finding_ids.extend(item.finding_id for item in record.findings)
            for assessment in record.condition_findings:
                assessments.append(
                    CaseAssessment(
                        case_id=light.case_id,
                        condition_id=assessment.condition_id,
                        status=assessment.status,
                        finding_ids=tuple(assessment.finding_ids),
                        span_ids=tuple(assessment.source_span_ids),
                        assessment_id="assessment."
                        + digest([light.case_id, assessment.condition_id]),
                    )
                )
            for claim in record.claims:
                invoked.extend(claim.invoked_rule_ids)
                claims.append(
                    CaseClaimView(
                        claim_id=claim.claim_id,
                        claim_type=claim.claim_type,
                        requested_remedy=claim.requested_remedy,
                        invoked_rule_ids=tuple(claim.invoked_rule_ids),
                    )
                )
            for step in record.reasoning_steps:
                applied.extend(step.applied_rule_ids)
                reasoning.append(
                    CaseReasoningView(
                        reasoning_id=step.reasoning_id,
                        applied_rule_ids=tuple(step.applied_rule_ids),
                        conclusion=step.conclusion,
                        span_ids=tuple(step.source_span_ids),
                    )
                )
            for decision in record.decisions:
                decisions.append(
                    CaseDecisionView(
                        decision_id=decision.decision_id,
                        claim_id=decision.claim_id,
                        status=decision.status,
                        description=decision.description,
                    )
                )
        case_docs[light.case_id] = CaseDocument(
            case_id=light.case_id,
            title=light.title,
            search_text=_case_search_text(light, record),
            cited_provision_ids=tuple(light.related_provision_ids),
            applied_rule_ids=tuple(dict.fromkeys(applied)),
            invoked_rule_ids=tuple(dict.fromkeys(invoked)),
            assessments=tuple(assessments),
            claims=tuple(claims),
            decisions=tuple(decisions),
            reasoning=tuple(reasoning),
            span_ids=tuple(dict.fromkeys(spans)),
            finding_predicates=tuple(predicates),
            finding_ids=tuple(finding_ids),
        )
    payload = {
        "release_id": release_id,
        "embedding_model": embedding_model,
        "rules": {key: doc.search_text for key, doc in sorted(rule_docs.items())},
        "cases": {key: doc.search_text for key, doc in sorted(case_docs.items())},
    }
    return RetrievalCatalog(
        release_id=release_id,
        index_version=digest(payload),
        embedding_model=embedding_model,
        rules=rule_docs,
        cases=case_docs,
        conditions=conditions,
        rule_ids_by_condition={key: tuple(dict.fromkeys(value)) for key, value in owners.items()},
        provision_text=provision_text,
        rule_records={item.rule_id: item for item in rules},
        case_records=deep_by_id,
    )


def load_canonical_catalog(data_root: Path, *, embedding_model: str = "none") -> RetrievalCatalog:
    from casepath.ingestion.graph.projection import load_projection

    # 读取发布信息
    bundle = load_projection(data_root)
    # 拼成内存目录
    return build_catalog(
        release_id=bundle["release_id"],
        # 规则json
        rules=read_jsonl(data_root / "canonical/rules/rules.jsonl", RuleRecord),
        # 法条json
        provisions=read_jsonl(data_root / "canonical/rules/provisions.jsonl", ProvisionRecord),
        # 轻量案例json
        lightweight=read_jsonl(data_root / "canonical/cases/cases_l1.jsonl", CaseIndexRecord),
        # 深度案例json
        deep=read_jsonl(data_root / "canonical/cases/cases_deep.jsonl", CaseRecord),
        # 这轮数据声明对应的是哪个 embedding 模型
        embedding_model=embedding_model,
    )


def bind_context(context: RetrievalContext) -> Token:
    return _CONTEXT.set(context)


def reset_context(token: Token | None) -> None:
    if token is not None:
        _CONTEXT.reset(token)


def current_context() -> RetrievalContext | None:
    return _CONTEXT.get()


def require_context() -> RetrievalContext:
    context = _CONTEXT.get()
    if context is None:
        raise RuntimeError("A/B/C 必须在同一 RetrievalContext 中运行")
    return context


def bind_round(evidence: RoundEvidence) -> Token:
    return _ROUND.set(evidence)


def current_round() -> RoundEvidence | None:
    return _ROUND.get()


def reset_round(token: Token | None) -> None:
    if token is not None:
        _ROUND.reset(token)


class MemoryHybridSearch:
    """同一对象空间上的全文检索；向量通道由调用方按同一 ID 集合补齐。"""

    def __init__(self, catalog: RetrievalCatalog) -> None:
        self.catalog = catalog
        self.rule_index = BM25Index({key: doc.search_text for key, doc in catalog.rules.items()})
        self.case_index = BM25Index({key: doc.search_text for key, doc in catalog.cases.items()})

    def search(
        self,
        text: str,
        *,
        target: Literal["rule", "case"],
        context: RetrievalContext,
        top_k: int,
    ) -> list[ScoredReference]:
        if context.release_id != self.catalog.release_id:
            raise ValueError("search index is not isolated to the request release")
        if context.index_version != self.catalog.index_version:
            raise ValueError("search index version does not match the request context")
        index = self.rule_index if target == "rule" else self.case_index
        hits = index.search(text, top_k)
        return [
            scored_reference(
                oid,
                score,
                channels=["bm25"],
                components=[
                    ScoreComponent(name="bm25", value=score, explanation="自建BM25原文检索")
                ],
                reasons=["全文通道命中"],
            )
            for oid, score in hits
        ]
