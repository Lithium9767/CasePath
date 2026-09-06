"""P4 实现区：用户事实到规则条件的语义投影。

词法只生成候选。区分意愿、事实、否定和条件句。LLM 判定允许替换词法或标记冲突。
quoted_span 必须是用户原文子串。同一事实对同一条件只保留一条证据。
"""

from __future__ import annotations

import re

from casepath.adapters.retrieval_catalog import RetrievalCatalog
from casepath.application.errors import LanguageModelUnavailable
from casepath.contracts import (
    CandidateClaim,
    ConditionEvidence,
    ConditionStatus,
    ContractModel,
    QueryConditionState,
    QueryState,
    ScoreComponent,
    ScoredReference,
    UserFact,
)
from casepath.ports import StructuredLanguageModel

_CLAIM_CUES = ("健身", "办卡", "充了", "余额", "关门", "停业", "会员", "服务")
_INTENT = ("想", "希望", "打算", "准备", "想要", "要求")
_CONDITIONAL = ("如果", "若", "假如", "要是")
_PATTERNS: dict[str, dict[str, tuple[str, ...]]] = {
    "cond.contract_exists": {
        "SUPPORTS": ("办了卡", "办卡", "签订合同", "签了合同", "会员协议", "在健身房", "存在合同"),
        "OPPOSES": ("没有合同", "未签订", "没有协议", "没有成立"),
    },
    "cond.payment_made": {
        "SUPPORTS": ("已经付款", "已经支付", "付了款", "交了费", "充了", "支付了", "付款了"),
        "OPPOSES": ("没有支付", "没有付款", "还没付款", "还没付", "尚未付款", "未付款", "没付款"),
    },
    "cond.unperformed_balance": {
        "SUPPORTS": ("未消费余额", "还有未消费", "还有余额", "没消费完", "剩余余额", "未消费"),
        "OPPOSES": ("已经全部消费", "全部消费", "消费完了", "没有余额", "无余额"),
    },
    "cond.performance_impossible": {
        "SUPPORTS": ("关门了", "已经关门", "停业", "关闭", "无法继续", "永久关闭"),
        "OPPOSES": ("仍能继续", "还能履行", "没有关门"),
    },
    "cond.alternative_performance": {
        "SUPPORTS": ("其他店", "可以转店", "替代服务", "另一家"),
        "OPPOSES": ("没有替代", "不能转店", "没有其他店"),
    },
    "cond.contract_terminated": {
        "SUPPORTS": ("已经解除", "解除了", "通知解除", "被确认解除"),
        "OPPOSES": ("尚未解除", "没有解除", "未解除"),
    },
    "cond.main_obligation_delayed": {
        "SUPPORTS": ("迟延", "逾期", "一直没履行", "拖延"),
        "OPPOSES": ("没有迟延", "按期履行"),
    },
    "cond.demand_delivered": {
        "SUPPORTS": ("催告", "催过", "通知履行"),
        "OPPOSES": ("没有催告", "未催告"),
    },
    "cond.reasonable_period_expired": {
        "SUPPORTS": ("过了期限", "合理期限届满", "催了还是"),
        "OPPOSES": ("仍在期限内", "未过期限"),
    },
}


class ConditionLinkDraft(ContractModel):
    # 新实现显式定位事实；兼容旧输出时，仅接受唯一的非空引句匹配。
    fact_id: str | None = None
    condition_id: str
    relation: str
    quoted_span: str
    rationale: str
    raw_confidence: float


class ConditionLinkBatch(ContractModel):
    links: list[ConditionLinkDraft]


def _fact_from_query(state: QueryState) -> list[UserFact]:
    if any(item.source_turn == 0 for item in state.user_facts):
        return list(state.user_facts)
    return [
        *state.user_facts,
        UserFact(fact_id="fact.initial.query", text=state.initial_query, source_turn=0),
    ]


def _is_intent_or_conditional(text: str) -> bool:
    return any(token in text for token in (*_INTENT, *_CONDITIONAL))


def _longest(text: str, phrases: tuple[str, ...]) -> str | None:
    found = [phrase for phrase in phrases if phrase and phrase in text]
    if not found:
        return None
    return max(found, key=len)


def _lexical_candidates(fact: UserFact, condition_id: str) -> list[ConditionEvidence]:
    # 句号/分号才切断假设范围，避免把“如果……，那么……”后半句当事实。
    sentences = re.split(r"[。！？!?；;\n]+", fact.text)
    factual = [text for text in sentences if not _is_intent_or_conditional(text)]
    text = "。".join(factual)
    patterns = _PATTERNS.get(condition_id)
    if patterns is None:
        return []
    oppose = _longest(text, patterns.get("OPPOSES", ()))
    support = _longest(text, patterns.get("SUPPORTS", ()))
    if oppose and support:
        # 仅在否定短语确实包住肯定短语时消解；跨句冲突不按长度裁决。
        if support in oppose and support not in text.replace(oppose, ""):
            support = None
        else:
            return []
    if support:
        positions = [match.start() for match in re.finditer(re.escape(support), text)]
        if any(re.search(r"(?:没有|并非|不是|未|不曾|没)\s*$", text[:pos]) for pos in positions):
            return []
    if oppose:
        return [
            ConditionEvidence(
                fact_id=fact.fact_id,
                relation="OPPOSES",
                confidence=0.58,
                reason=f"词法候选：原话含「{oppose}」",
            )
        ]
    if support:
        return [
            ConditionEvidence(
                fact_id=fact.fact_id,
                relation="SUPPORTS",
                confidence=0.58,
                reason=f"词法候选：原话含「{support}」",
            )
        ]
    return []


def _aggregate(
    condition_id: str,
    evidence: list[ConditionEvidence],
    *,
    reasons: list[str],
    conflicting: bool = False,
) -> QueryConditionState:
    supports = [item for item in evidence if item.relation == "SUPPORTS"]
    opposes = [item for item in evidence if item.relation == "OPPOSES"]
    unique = list({item.fact_id: item for item in evidence}.values())
    if conflicting or (supports and opposes):
        status = ConditionStatus.CONFLICTING
        confidence = min(0.7, max((item.confidence for item in unique), default=0.5))
    elif supports:
        status = ConditionStatus.SATISFIED
        confidence = max(item.confidence for item in supports)
    elif opposes:
        status = ConditionStatus.NOT_SATISFIED
        confidence = max(item.confidence for item in opposes)
    else:
        status = ConditionStatus.UNKNOWN
        confidence = None
    return QueryConditionState(
        condition_id=condition_id,
        status=status,
        supporting_fact_ids=[item.fact_id for item in unique],
        confidence=confidence,
        evidence=unique,
        mapping_reasons=reasons,
        score_components=[
            ScoreComponent(
                name="lexical_overlap",
                value=confidence or 0.0,
                explanation="条件命题与用户原话重合的候选分数，不是胜诉概率",
            )
        ],
        last_updated_turn=0,
    )


class SemanticConditionProjector:
    def __init__(
        self,
        catalog: RetrievalCatalog,
        language_model: StructuredLanguageModel | None = None,
    ) -> None:
        self.catalog = catalog
        self.language_model = language_model

    def project(self, state: QueryState, rule_refs: list[ScoredReference]) -> QueryState:
        allowed: list[str] = []
        for ref in rule_refs:
            rule = self.catalog.rules.get(ref.object_id)
            if rule is not None:
                allowed.extend(rule.condition_ids)
        allowed = list(dict.fromkeys(allowed))
        if not allowed:
            allowed = list(self.catalog.conditions)
        facts = _fact_from_query(state)
        existing = {item.condition_id: item for item in state.condition_states}
        links: dict[str, dict[str, ConditionEvidence]] = {key: {} for key in allowed}
        for fact in facts:
            for condition_id in allowed:
                for evidence in _lexical_candidates(fact, condition_id):
                    links[condition_id][fact.fact_id] = evidence
        conflicts: set[str] = set()
        reasons = ["词法仅生成候选；无LLM时采用保守否定/意愿过滤"]
        if self.language_model is not None:
            self._merge_llm(state, facts, allowed, links, conflicts)
            reasons = ["词法候选经LLM判定替换或标记冲突"]
        states = []
        for condition_id in allowed:
            prior = existing.get(condition_id)
            if prior is not None and prior.last_updated_turn > 0:
                states.append(prior)
                continue
            evidence = list(links[condition_id].values())
            if evidence:
                states.append(
                    _aggregate(
                        condition_id,
                        evidence,
                        reasons=reasons,
                        conflicting=condition_id in conflicts,
                    )
                )
            else:
                states.append(
                    QueryConditionState(
                        condition_id=condition_id,
                        status=ConditionStatus.UNKNOWN,
                        mapping_reasons=["尚无可用原文证据"],
                        last_updated_turn=0,
                    )
                )
        claims = state.candidate_claims or self._guess_claims(state.initial_query)
        return state.model_copy(
            update={"user_facts": facts, "candidate_claims": claims, "condition_states": states}
        )

    def _guess_claims(self, query: str) -> list[CandidateClaim]:
        if any(cue in query for cue in _CLAIM_CUES):
            return [
                CandidateClaim(
                    claim_type="服务合同解除与返还",
                    requested_remedy="解除服务合同并处理未消费费用",
                    confidence=0.7,
                )
            ]
        return [
            CandidateClaim(
                claim_type="民事咨询",
                requested_remedy=query[:40],
                confidence=0.4,
            )
        ]

    def _merge_llm(
        self,
        state: QueryState,
        facts: list[UserFact],
        allowed: list[str],
        links: dict[str, dict[str, ConditionEvidence]],
        conflicts: set[str],
    ) -> None:
        try:
            draft = self.language_model.generate_structured(
                system_prompt=(
                    "只从给定 condition_id 中选择，返回 fact_id；quoted_span 必须非空且逐字"
                    "摘自该事实。假设、意愿不是已发生事实，无证据则不输出映射。"
                ),
                user_prompt=(
                    f"允许条件: {allowed}\n"
                    f"条件定义: {[(key, self.catalog.conditions[key].predicate) for key in allowed]}\n"
                    f"事实: {[item.model_dump() for item in facts]}\n"
                    f"问题: {state.initial_query}"
                ),
                response_model=ConditionLinkBatch,
            )
        except LanguageModelUnavailable:
            raise
        except Exception as exc:
            raise LanguageModelUnavailable("条件映射模型调用失败") from exc
        allowed_set = set(allowed)
        fact_texts = {item.fact_id: item.text for item in facts}
        for item in draft.links:
            if item.condition_id not in allowed_set or not item.quoted_span.strip():
                continue
            owners = [
                fact_id
                for fact_id, text in fact_texts.items()
                if item.quoted_span in text and (item.fact_id is None or item.fact_id == fact_id)
            ]
            owner = owners[0] if len(owners) == 1 else None
            if owner is None or item.relation not in {"SUPPORTS", "OPPOSES", "QUALIFIES"}:
                continue
            llm_evidence = ConditionEvidence(
                fact_id=owner,
                relation=item.relation,
                confidence=min(max(item.raw_confidence, 0.0), 1.0),
                reason=item.rationale[:80] or "受约束LLM映射",
            )
            prior = links[item.condition_id].get(owner)
            if prior is not None and prior.relation != item.relation:
                conflicts.add(item.condition_id)
            links[item.condition_id][owner] = llm_evidence
