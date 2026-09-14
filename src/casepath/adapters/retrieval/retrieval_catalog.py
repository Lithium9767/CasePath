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

_TOKEN = re.compile(r"[0-9A-Za-z_]+|[\u4e00-\u9fff]+")
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


@dataclass(frozen=True)
class RuleScope:
    """单轮活动范围：A 写入，B/C/D/E/解释读取。只存 ID，不复制规则对象。

    与 RoundEvidence 分工：本对象描述规则用途；RoundEvidence 描述案例证据池。
    生命周期为一次 workflow.run，必须 finally 清理，不得写入共享 retriever 实例字段。
    """

    active_rule_ids: tuple[str, ...]
    alternative_rule_ids: tuple[str, ...]
    background_rule_ids: tuple[str, ...]
    active_condition_ids: tuple[str, ...]


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
    # 未定角色原因：case_id → 稳定原因码，供 D/解释消费，不另造候选池。
    undetermined_reasons: dict[str, str] = field(default_factory=dict)
    compared_rule_ids: tuple[str, ...] = ()


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
    """字符及相邻双字基线；标点、空白、数字和拉丁字串都是汉字邻接边界。"""
    tokens: list[str] = []
    for part in _TOKEN.findall(text.lower()):
        if "\u4e00" <= part[0] <= "\u9fff":
            tokens.extend(part)
            tokens.extend(a + b for a, b in pairwise(part))
        else:
            tokens.append(part)
    return tokens


class BM25Index:
    """倒排 BM25 字符基线；相邻双字为主，单字只作低权重的词面回退。

    查询保留相对词频（词频/总词数）：整句重复不加分，句内主题重复仍有权重。
    k1/b/单字权重均为可解释工程默认值，不声称经盲测优化或是 BM25F。
    """

    def __init__(
        self,
        documents: dict[str, str],
        *,
        k1: float = 1.2,
        b: float = 0.75,
        han_unigram_weight: float = 0.25,
    ) -> None:
        # 建立词项统计：少见词的 IDF 更高，长文通过长度归一化避免天然占优。
        # 索引时缓存词频和长度项；搜索仅访问命中词的倒排，不反复重建全库 Counter。
        if (
            not all(math.isfinite(value) for value in (k1, b, han_unigram_weight))
            or k1 <= 0
            or not 0 <= b <= 1
            or not 0 < han_unigram_weight <= 1
        ):
            raise ValueError("BM25 parameters must be valid")
        self.k1 = k1
        self.b = b
        self.han_unigram_weight = han_unigram_weight
        self.ids = sorted(documents)
        self.tokens = {oid: tokenize(documents[oid]) for oid in self.ids}
        self.n = len(self.ids)
        lengths = [len(self.tokens[oid]) or 1 for oid in self.ids]
        self.avgdl = sum(lengths) / len(lengths) if lengths else 1.0
        postings: dict[str, list[tuple[str, int]]] = defaultdict(list)
        self._length_norm: dict[str, float] = {}
        for oid in self.ids:
            for term, frequency in Counter(self.tokens[oid]).items():
                postings[term].append((oid, frequency))
            dl = len(self.tokens[oid]) or 1
            self._length_norm[oid] = self.k1 * (1.0 - self.b + self.b * dl / self.avgdl)
        self._postings = {term: tuple(rows) for term, rows in postings.items()}
        self.idf = {
            term: math.log((self.n - len(rows) + 0.5) / (len(rows) + 0.5) + 1.0)
            for term, rows in self._postings.items()
        }

    def search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        if type(top_k) is not int or top_k <= 0:
            raise ValueError("top_k must be positive")
        q_terms = tokenize(query)
        if not q_terms or not self.ids:
            return []
        query_frequencies: dict[str, int] = defaultdict(int)
        for term in q_terms:
            query_frequencies[term] += 1
        scores: dict[str, float] = defaultdict(float)
        for term, query_frequency in query_frequencies.items():
            # 归一化保留句内相对重点；重复整句只同比增加分子和分母，不刷分。
            query_weight = query_frequency / len(q_terms)
            # 不使用领域停用词表；“不”等否定单字仍保留，其邻接双字不被删掉。
            weight = (
                self.han_unigram_weight
                if len(term) == 1 and "\u4e00" <= term <= "\u9fff"
                else 1.0
            )
            for oid, frequency in self._postings.get(term, ()):
                denominator = frequency + self._length_norm[oid]
                scores[oid] += (
                    query_weight * weight * self.idf[term]
                    * frequency * (self.k1 + 1.0) / denominator
                )
        scored = [(oid, score) for oid, score in scores.items() if score > 0]
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


def _distinct_search_chunks(chunks: Iterable[str]) -> list[str]:
    """只移除逐字重复或被其他字段完整包含的片段，不合并不同法律命题。"""
    unique = list(dict.fromkeys(" ".join(chunk.split()) for chunk in chunks if chunk.strip()))
    return [
        chunk for chunk in unique
        if not any(chunk != other and chunk in other for other in unique)
    ]


def _rule_search_text(rule: RuleRecord, provision_text: dict[str, str]) -> str:
    # 【c 索引文本】只使用已有规范字段；不从测试问题补写同义词或新法律条件。
    # BM25 与 embedding 共用 search_text；不是对图中每个节点做图嵌入。
    chunks = [rule.title, *rule.claim_types]
    for condition in rule.conditions:
        # 当前合同没有 user_question 字段，predicate 本身已有可回答的事实问题。
        chunks.extend([condition.label, condition.predicate])
    for exception in rule.exceptions:
        chunks.extend([exception.label, exception.predicate, exception.effect])
    for consequence in rule.consequences:
        chunks.extend([consequence.consequence_type, consequence.description])
    for ref in rule.provisions:
        chunks.append(provision_text.get(ref.provision_id, ""))
        chunks.append(ref.title)
    for span in rule.source_spans:
        chunks.append(span.quote)
    # 完整法条仍在；已包含于法条的 span/条件短句不再复制一次制造额外词频。
    return "\n".join(_distinct_search_chunks(chunks))


def _case_search_text(light: CaseIndexRecord, deep: CaseRecord | None) -> str:
    from casepath.adapters.retrieval.case_facts import extract_case_fact_prefix

    # 一案一文本：保留已有标题/案由/关键词/查明，并纳入事实正文的保守前缀。
    # 不追加 summary、说理、裁判、条件状态或支持/限制角色；已识别的裁判后缀截断。
    # 既有元数据和查明仍可能含法律标签，不能将此改动宣称为全数据无泄漏。
    facts = extract_case_fact_prefix(light.facts_text)
    parts = [light.title, light.cause or "", *light.keywords, facts]
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


_SCOPE: ContextVar[RuleScope | None] = ContextVar("casepath_rule_scope", default=None)


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


def bind_round(evidence: RoundEvidence | None) -> Token:
    """绑定本轮案例证据；允许 None 作为请求外层空槽位。"""
    return _ROUND.set(evidence)


def set_round(evidence: RoundEvidence | None) -> None:
    """在外层已拥有 token 的作用域内更新本轮证据，不另开嵌套 token。"""
    _ROUND.set(evidence)


def current_round() -> RoundEvidence | None:
    return _ROUND.get()


def reset_round(token: Token | None) -> None:
    if token is not None:
        _ROUND.reset(token)


def bind_scope(scope: RuleScope | None) -> Token:
    """绑定规则用途；允许 None 作为请求外层空槽位。"""
    return _SCOPE.set(scope)


def set_scope(scope: RuleScope | None) -> None:
    """在外层已拥有 token 的作用域内更新 RuleScope，不另开嵌套 token。"""
    _SCOPE.set(scope)


def reset_scope(token: Token | None) -> None:
    if token is not None:
        _SCOPE.reset(token)


def clear_scope() -> None:
    _SCOPE.set(None)


def clear_round() -> None:
    _ROUND.set(None)


def current_scope() -> RuleScope | None:
    return _SCOPE.get()


def require_scope() -> RuleScope:
    scope = _SCOPE.get()
    if scope is None:
        raise RuntimeError("RuleScope 未绑定：须由 A 写入或由测试显式注入，禁止无声回退为全部规则")
    return scope


def validate_rule_scope(scope: RuleScope, catalog: RetrievalCatalog) -> None:
    """校验三种用途互不重叠、ID 存在、活动条件属于活动规则。"""
    active = list(scope.active_rule_ids)
    alternative = list(scope.alternative_rule_ids)
    background = list(scope.background_rule_ids)
    for name, values in (
        ("active", active),
        ("alternative", alternative),
        ("background", background),
        ("active_conditions", list(scope.active_condition_ids)),
    ):
        if len(values) != len(set(values)):
            raise ValueError(f"RuleScope.{name} contains duplicates")
    bags = (set(active), set(alternative), set(background))
    if any(left & right for i, left in enumerate(bags) for right in bags[i + 1 :]):
        raise ValueError("RuleScope rule usage sets must be disjoint")
    for rule_id in (*active, *alternative, *background):
        if rule_id not in catalog.rules:
            raise ValueError(f"RuleScope references unknown rule {rule_id}")
    allowed_conditions = {
        condition_id
        for rule_id in active
        for condition_id in catalog.rules[rule_id].condition_ids
    }
    for condition_id in scope.active_condition_ids:
        if condition_id not in allowed_conditions:
            raise ValueError(f"active condition {condition_id} is not owned by an active rule")


class MemoryHybridSearch:
    """同一对象空间上的全文检索；向量通道由调用方按同一 ID 集合补齐。"""

    def __init__(
        self, catalog: RetrievalCatalog, *, case_han_unigram_weight: float = 0.25,
        case_feedback: bool = False,
    ) -> None:
        if type(case_feedback) is not bool:
            raise ValueError("case_feedback must be a boolean")
        self.catalog = catalog
        self.rule_index = BM25Index({key: doc.search_text for key, doc in catalog.rules.items()})
        self.case_index = BM25Index(
            {key: doc.search_text for key, doc in catalog.cases.items()},
            han_unigram_weight=case_han_unigram_weight,
        )
        self.case_feedback = case_feedback
        self._case_feedback_index = None
        if case_feedback:
            from casepath.adapters.retrieval.local_feedback import LocalFeedbackIndex

            self._case_feedback_index = LocalFeedbackIndex(self.case_index)

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
        terms = ()
        if target == "case" and self._case_feedback_index is not None:
            result = self._case_feedback_index.search(text, top_k)
            hits, terms = result.hits, result.terms
        else:
            hits = index.search(text, top_k)
        return [
            scored_reference(
                oid,
                score,
                channels=["bm25"],
                components=[
                    ScoreComponent(
                        name="bm25", value=score,
                        explanation=("BM25正文检索加0.2弱反馈；归一化词法分，非概率"
                                     if terms else "自建BM25原文检索"),
                    )
                ],
                reasons=["全文通道命中", *(["本地反馈词（非用户事实）：" + "、".join(terms)]
                                          if terms else [])],
            )
            for oid, score in hits
        ]
