"""Machine extraction of claims, findings, condition bridges, and holdings."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from casepath.contracts import (
    ClaimRecord,
    ConditionFinding,
    ConditionStatus,
    CourtFinding,
    DecisionItem,
    DecisionStatus,
    ReasoningStep,
    SourceSpan,
)
from casepath.rule_layer.ids import (
    COND_ALTERNATIVE_PERFORMANCE,
    COND_CONTRACT_EXISTS,
    COND_CONTRACT_TERMINATED,
    COND_PAYMENT_MADE,
    COND_PERFORMANCE_IMPOSSIBLE,
    COND_UNPERFORMED_BALANCE,
    RULE_SERVICE_TERMINATION_REFUND,
)

from .classify import is_service_termination_refund
from .ids import CLAIM_TYPE_GENERIC, CLAIM_TYPE_SERVICE_TERMINATION_REFUND
from .sections import (
    FactPartitions,
    court_finding_sentences,
    numbered_fact_items,
    parse_granted_amount,
    parse_requested_amount,
)

_CONTRACT_RE = re.compile(r"签订.{0,20}(协议|合同)")
# "购买/约定购买" alone is not proof of payment; require transfer/receipt language.
_SERVICE_PAYMENT_RE = re.compile(
    r"(支付|交纳|缴纳|付款).{0,24}(会籍|私教|会员|服务费|课程|服务|培训费|旅游费|团费)|"
    r"(会籍|私教|会员|服务费|课程|培训费|旅游费|团费).{0,16}(支付|交纳|缴纳|付款)|"
    r"出具.{0,16}收据|收据.{0,40}(金额|元)"
)
_REPAIR_FEE_RE = re.compile(r"维修费|停车管理费")
_IMPOSSIBLE_RE = re.compile(r"关闭|闭店|撤离|无法提供|不能提供|停止营业|相继关闭|未正式开业")
_STILL_OPEN_RE = re.compile(r"正常营业|继续提供|仍可提供")
_UNPERFORMED_RE = re.compile(r"剩余|未消费|未履行|未使用|未开始上课|尚有\d+次课|尚有.{0,8}次课")
_ALTERNATIVE_RE = re.compile(r"转让|安置|另一.{0,8}(会所|门店)|替代")
_ALTERNATIVE_FAIL_RE = re.compile(r"距离较远|不便于|未经.{0,8}同意")
_TERMINATED_RE = re.compile(r"解除.{0,20}(协议|合同)")
_REJECT_ALL_RE = re.compile(r"驳回原告.{0,20}诉讼请求")
_REJECT_OTHER_RE = re.compile(r"驳回.{0,8}其他诉讼请求")
_APPLIES_TERMINATION_REFUND_RE = re.compile(
    r"(解除.{0,30}符合法律规定)|"
    r"(根据《中华人民共和国民法典》第五百六十三.{0,40}解除)|"
    r"(根据《中华人民共和国民法典》第五百六十六.{0,40}返还)"
)


@dataclass
class SpanBook:
    case_id: str
    spans: list[SourceSpan] = field(default_factory=list)

    def _next_index(self) -> int:
        indexes: list[int] = []
        prefix = f"{self.case_id}.span."
        for span in self.spans:
            if not span.span_id.startswith(prefix):
                continue
            suffix = span.span_id[len(prefix) :]
            if suffix.isdigit():
                indexes.append(int(suffix))
        return (max(indexes) if indexes else 0) + 1

    def add(
        self,
        section: str,
        document: str,
        quote: str,
        region: tuple[int, int] | None = None,
    ) -> SourceSpan:
        quote = quote.strip()
        for existing in self.spans:
            if existing.section == section and existing.quote == quote:
                return existing
        start, end = locate_quote(document, quote, region)
        index = self._next_index()
        span = SourceSpan(
            span_id=entity_id(self.case_id, "span", index),
            source_id=f"case.{self.case_id}",
            section=section,
            paragraph_id=f"{section}-{index:03d}",
            start_offset=start,
            end_offset=end,
            quote=quote,
        )
        self.spans.append(span)
        return span


def entity_id(case_id: str, kind: str, index: int) -> str:
    return f"{case_id}.{kind}.{index:03d}"


def locate_quote(
    document: str,
    quote: str,
    region: tuple[int, int] | None = None,
) -> tuple[int, int]:
    start = 0
    end = len(document)
    if region is not None:
        start, end = region
        found = document.find(quote, start, end)
        if found >= 0:
            return found, found + len(quote)
    found = document.find(quote)
    if found < 0:
        raise ValueError(f"quote not found in {region}: {quote[:40]}")
    return found, found + len(quote)


def extract_parties(title: str, partitions: FactPartitions) -> tuple[str | None, str | None]:
    match = re.match(r"(.+?)诉(.+)案$", title)
    claimant = match.group(1).strip() if match else None
    respondent = match.group(2).strip() if match else None
    if respondent:
        for suffix in ("服务合同纠纷", "合同纠纷", "纠纷"):
            if respondent.endswith(suffix):
                respondent = respondent[: -len(suffix)].strip()
                break
    if partitions.plaintiff and claimant is None:
        claimant = "原告"
    if partitions.defendant and respondent is None:
        respondent = "被告"
    return claimant or None, respondent or None


def extract_claim(
    case_id: str,
    case: dict[str, object],
    partitions: FactPartitions,
    title: str,
) -> ClaimRecord:
    plaintiff = partitions.plaintiff
    claim_type = (
        CLAIM_TYPE_SERVICE_TERMINATION_REFUND
        if is_service_termination_refund(case, plaintiff)
        else str(case.get("case_cause") or CLAIM_TYPE_GENERIC)
    )
    if is_service_termination_refund(case, plaintiff):
        remedy = _requested_remedy(plaintiff) or "解除服务合同并返还未履行服务费用"
        invoked = [RULE_SERVICE_TERMINATION_REFUND]
    else:
        remedy = _requested_remedy(plaintiff) or str(case.get("case_cause") or CLAIM_TYPE_GENERIC)
        invoked = []
    claimant, respondent = extract_parties(title, partitions)
    return ClaimRecord(
        claim_id=entity_id(case_id, "claim", 1),
        claim_type=claim_type,
        claimant=claimant,
        respondent=respondent,
        requested_remedy=remedy,
        amount=parse_requested_amount(plaintiff),
        invoked_rule_ids=invoked,
    )


def extract_findings(
    case_id: str,
    partitions: FactPartitions,
    facts_text: str,
    book: SpanBook,
) -> list[CourtFinding]:
    findings: list[CourtFinding] = []
    region = _region_of(facts_text, partitions.court)
    for sentence in court_finding_sentences(partitions.court):
        try:
            span = book.add("基本案情", facts_text, sentence, region)
        except ValueError:
            continue
        findings.append(
            CourtFinding(
                finding_id=entity_id(case_id, "finding", len(findings) + 1),
                predicate=sentence,
                polarity=True,
                source_span_ids=[span.span_id],
            )
        )
    return findings


def extract_findings_from_facts(
    case_id: str,
    facts_text: str,
    book: SpanBook,
) -> list[CourtFinding]:
    """Build court findings from numbered recognized facts, not 诉称/辩称."""

    items = numbered_fact_items(facts_text)
    if not items:
        items = court_finding_sentences(facts_text)
    compact = facts_text.strip()
    if not items and len(compact) >= 8:
        items = [compact]
    findings: list[CourtFinding] = []
    for sentence in items:
        try:
            span = book.add("基本案情", facts_text, sentence)
        except ValueError:
            continue
        findings.append(
            CourtFinding(
                finding_id=entity_id(case_id, "finding", len(findings) + 1),
                predicate=sentence,
                polarity=True,
                source_span_ids=[span.span_id],
            )
        )
    return findings


def extract_reasoning_step(
    case_id: str,
    reasoning_text: str,
    findings: list[CourtFinding],
    book: SpanBook,
) -> ReasoningStep | None:
    # 适用关系与引用必须来自同一句肯定说理，不能扫描全文后挂到另一句话。
    application_sentence = application_evidence(reasoning_text)
    sentence = application_sentence or _key_reasoning_sentence(reasoning_text)
    if not sentence:
        return None
    try:
        span = book.add("裁判理由", reasoning_text, sentence)
    except ValueError:
        return None
    return ReasoningStep(
        reasoning_id=entity_id(case_id, "reasoning", 1),
        premise_finding_ids=[item.finding_id for item in findings[:1]],
        # APPLIES must be grounded in court reasoning, never copied from claim INVOKES.
        applied_rule_ids=[RULE_SERVICE_TERMINATION_REFUND] if application_sentence else [],
        conclusion=sentence,
        source_span_ids=[span.span_id],
    )


def applied_rule_ids_from_reasoning(reasoning_text: str) -> list[str]:
    """只为可定位的法院肯定说理返回规则，不从诉讼请求复制关系。"""

    return [RULE_SERVICE_TERMINATION_REFUND] if application_evidence(reasoning_text) else []


def application_evidence(reasoning_text: str) -> str | None:
    """保守的 L1 规则适用识别，返回实际证明适用的原句。

    这不是完整法律推理器。存在否定/假设或角色不清时宁可不建边；以后应
    按说理步骤精确标注，不能把降低召回率误认为法院没有适用该规则。
    """

    text = reasoning_text or ""
    # 包含跨句转折否定的段落也不建边，避免先引用主张、下一句再驳回。
    if re.search(r"不予支持|不予采纳|不予认可|不支持|不成立|不适用|不能适用|不应适用|"
                 r"不符合法律规定|并非|并不|无需返还|无须返还|不得返还|驳回|假如|如果|假设", text):
        return None
    for match in re.finditer(r"[^。；\n]+[。；]?", text):
        sentence = match.group(0).strip()
        if not _APPLIES_TERMINATION_REFUND_RE.search(sentence):
            continue
        # 引用当事人观点本身不足以构成法院适用，即使引句含“符合法律规定”。
        if re.search(r"主张|诉称|辩称|认为|称", sentence) and not sentence.startswith(
            ("本院认为", "法院认为", "本院认定")
        ):
            continue
        # 无法条的宽泛“解除合法”不能挂到特定服务退款规则。
        if not re.search(r"第五百六十[三六]条|第(?:563|566)条", sentence):
            continue
        if re.search(r"符合法律规定|应予返还|应当返还|应返还|予以支持|本院支持", sentence):
            return sentence
    return None


def extract_decision(
    case_id: str,
    claim: ClaimRecord,
    partitions: FactPartitions,
    facts_text: str,
    reasoning_text: str,
    book: SpanBook,
) -> DecisionItem | None:
    holding = partitions.holding or ""
    quote = _holding_quote(holding)
    if not quote:
        return None
    document = facts_text if holding and holding in facts_text else reasoning_text
    section = "裁判结果" if holding and holding in facts_text else "裁判理由"
    if quote not in document:
        if quote in facts_text:
            document, section = facts_text, "裁判结果"
        elif quote in reasoning_text:
            document, section = reasoning_text, "裁判理由"
        else:
            return None
    span = book.add(section, document, quote)
    granted = parse_granted_amount(holding)
    status = decide_status(claim.amount, granted, holding, claim.requested_remedy)
    return DecisionItem(
        decision_id=entity_id(case_id, "decision", 1),
        claim_id=claim.claim_id,
        status=status,
        description=quote,
        amount=granted,
        source_span_ids=[span.span_id],
    )


def decide_status(
    requested: float | None,
    granted: float | None,
    holding: str,
    remedy: str,
) -> DecisionStatus:
    if not holding.strip():
        return DecisionStatus.UNKNOWN
    if _REJECT_ALL_RE.search(holding) and granted is None:
        return DecisionStatus.REJECTED
    if requested is not None and granted is not None:
        if granted + 0.009 >= requested:
            if _REJECT_OTHER_RE.search(holding):
                return DecisionStatus.PARTIALLY_GRANTED
            return DecisionStatus.GRANTED
        if granted > 0:
            return DecisionStatus.PARTIALLY_GRANTED
    if _TERMINATED_RE.search(holding) and "解除" in remedy:
        if granted is not None and requested is not None and granted + 0.009 < requested:
            return DecisionStatus.PARTIALLY_GRANTED
        if _REJECT_OTHER_RE.search(holding):
            return DecisionStatus.PARTIALLY_GRANTED
        return DecisionStatus.GRANTED
    if granted is not None and granted > 0 and requested is None:
        return DecisionStatus.GRANTED
    return DecisionStatus.UNKNOWN


def extract_condition_findings(
    findings: list[CourtFinding],
    facts_text: str,
    reasoning_text: str,
    holding: str,
    book: SpanBook,
) -> list[ConditionFinding]:
    sentences = [item.predicate for item in findings]
    finding_by_predicate = {item.predicate: item for item in findings}
    bridges = [
        _bridge(
            COND_CONTRACT_EXISTS,
            _match_status(sentences, _CONTRACT_RE, None),
            finding_by_predicate,
            book,
            "基本案情",
            facts_text,
        ),
        _payment_bridge(sentences, finding_by_predicate, book, facts_text, reasoning_text),
        _bridge(
            COND_PERFORMANCE_IMPOSSIBLE,
            _match_status(sentences, _IMPOSSIBLE_RE, _STILL_OPEN_RE),
            finding_by_predicate,
            book,
            "基本案情",
            facts_text,
        ),
        _unperformed_bridge(sentences, facts_text, reasoning_text, finding_by_predicate, book),
        _alternative_bridge(sentences, finding_by_predicate, book, facts_text),
        _terminated_bridge(holding, facts_text, book),
    ]
    return bridges


def _payment_bridge(
    sentences: list[str],
    finding_by_predicate: dict[str, CourtFinding],
    book: SpanBook,
    facts_text: str,
    reasoning_text: str = "",
) -> ConditionFinding:
    service_hits = [sentence for sentence in sentences if _SERVICE_PAYMENT_RE.search(sentence)]
    repair_hits = [sentence for sentence in sentences if _REPAIR_FEE_RE.search(sentence)]
    if service_hits:
        return _bridge(
            COND_PAYMENT_MADE,
            (ConditionStatus.SATISFIED, service_hits[0], 0.7),
            finding_by_predicate,
            book,
            "基本案情",
            facts_text,
        )
    if repair_hits:
        return _unknown(COND_PAYMENT_MADE)
    # Court reasoning may recognize payment ("交纳") when findings only say "购买".
    for match in re.finditer(r"[^。；\n]+[。；]?", reasoning_text or ""):
        sentence = match.group(0).strip()
        if _SERVICE_PAYMENT_RE.search(sentence) or re.search(
            r"交纳的.{0,12}(会籍|课程|服务)?费用|已支付|已经支付",
            sentence,
        ):
            try:
                span = book.add("裁判理由", reasoning_text, sentence)
            except ValueError:
                break
            return ConditionFinding(
                condition_id=COND_PAYMENT_MADE,
                status=ConditionStatus.SATISFIED,
                finding_ids=[],
                confidence=0.55,
                source_span_ids=[span.span_id],
                human_verified=False,
            )
    return _unknown(COND_PAYMENT_MADE)


def _unperformed_bridge(
    sentences: list[str],
    facts_text: str,
    reasoning_text: str,
    finding_by_predicate: dict[str, CourtFinding],
    book: SpanBook,
) -> ConditionFinding:
    hits = [sentence for sentence in sentences if _UNPERFORMED_RE.search(sentence)]
    if hits:
        return _bridge(
            COND_UNPERFORMED_BALANCE,
            (ConditionStatus.SATISFIED, hits[0], 0.65),
            finding_by_predicate,
            book,
            "基本案情",
            facts_text,
        )
    reasoning_hits = [
        match.group(0).strip()
        for match in re.finditer(r"[^。；\n]+[。；]?", reasoning_text)
        if _UNPERFORMED_RE.search(match.group(0))
    ]
    if reasoning_hits:
        try:
            span = book.add("裁判理由", reasoning_text, reasoning_hits[0])
        except ValueError:
            return _unknown(COND_UNPERFORMED_BALANCE)
        return ConditionFinding(
            condition_id=COND_UNPERFORMED_BALANCE,
            status=ConditionStatus.SATISFIED,
            finding_ids=[],
            confidence=0.55,
            source_span_ids=[span.span_id],
            human_verified=False,
        )
    return _unknown(COND_UNPERFORMED_BALANCE)


def _alternative_bridge(
    sentences: list[str],
    finding_by_predicate: dict[str, CourtFinding],
    book: SpanBook,
    facts_text: str,
) -> ConditionFinding:
    fail_hits = [
        sentence
        for sentence in sentences
        if re.search(r"距离较远|不便于", sentence)
    ] or [sentence for sentence in sentences if _ALTERNATIVE_FAIL_RE.search(sentence)]
    offer_hits = [sentence for sentence in sentences if _ALTERNATIVE_RE.search(sentence)]
    if fail_hits:
        return _bridge(
            COND_ALTERNATIVE_PERFORMANCE,
            (ConditionStatus.NOT_SATISFIED, fail_hits[0], 0.65),
            finding_by_predicate,
            book,
            "基本案情",
            facts_text,
        )
    if offer_hits:
        return _bridge(
            COND_ALTERNATIVE_PERFORMANCE,
            (ConditionStatus.UNKNOWN, offer_hits[0], 0.4),
            finding_by_predicate,
            book,
            "基本案情",
            facts_text,
        )
    return _unknown(COND_ALTERNATIVE_PERFORMANCE)


def _terminated_bridge(holding: str, facts_text: str, book: SpanBook) -> ConditionFinding:
    quote = _holding_quote(holding)
    if quote and _TERMINATED_RE.search(quote):
        document = facts_text if quote in facts_text else quote
        try:
            span = book.add("裁判结果", document, quote)
        except ValueError:
            return _unknown(COND_CONTRACT_TERMINATED)
        return ConditionFinding(
            condition_id=COND_CONTRACT_TERMINATED,
            status=ConditionStatus.SATISFIED,
            finding_ids=[],
            confidence=0.6,
            source_span_ids=[span.span_id],
            human_verified=False,
        )
    return _unknown(COND_CONTRACT_TERMINATED)


def _match_status(
    sentences: list[str],
    positive: re.Pattern[str],
    negative: re.Pattern[str] | None,
) -> tuple[ConditionStatus, str | None, float]:
    for sentence in sentences:
        if positive.search(sentence):
            return ConditionStatus.SATISFIED, sentence, 0.7
    if negative is not None:
        for sentence in sentences:
            if negative.search(sentence):
                return ConditionStatus.NOT_SATISFIED, sentence, 0.65
    return ConditionStatus.UNKNOWN, None, 0.3


def _bridge(
    condition_id: str,
    matched: tuple[ConditionStatus, str | None, float],
    finding_by_predicate: dict[str, CourtFinding],
    book: SpanBook,
    section: str,
    document: str,
) -> ConditionFinding:
    status, quote, confidence = matched
    if quote is None:
        return _unknown(condition_id)
    finding = finding_by_predicate.get(quote)
    try:
        span = book.add(section, document if quote in document else quote, quote)
    except ValueError:
        return _unknown(condition_id)
    return ConditionFinding(
        condition_id=condition_id,
        status=status,
        finding_ids=[finding.finding_id] if finding is not None else [],
        confidence=confidence,
        source_span_ids=[span.span_id],
        human_verified=False,
    )


def _unknown(condition_id: str) -> ConditionFinding:
    return ConditionFinding(
        condition_id=condition_id,
        status=ConditionStatus.UNKNOWN,
        finding_ids=[],
        confidence=0.3,
        source_span_ids=[],
        human_verified=False,
    )


def _requested_remedy(plaintiff: str) -> str:
    match = re.search(r"请求判令[：:](.+)", plaintiff or "")
    if match:
        return match.group(1).strip()[:240]
    return _first_sentence(plaintiff)


def _first_sentence(text: str) -> str:
    match = re.search(r"[^。；\n]+[。；]?", text or "")
    return match.group(0).strip() if match else ""


def _key_reasoning_sentence(reasoning_text: str) -> str:
    sentences = [match.group(0).strip() for match in re.finditer(r"[^。；\n]+[。；]?", reasoning_text)]
    for sentence in sentences:
        if "解除" in sentence or "返还" in sentence or "符合法律规定" in sentence:
            return sentence
    return sentences[0] if sentences else ""


def _holding_quote(holding: str) -> str:
    if not holding:
        return ""
    match = re.search(r"判决[：:].+", holding)
    if match:
        return match.group(0).strip()[:240]
    return holding.strip()[:240]


def _region_of(document: str, fragment: str) -> tuple[int, int] | None:
    if not fragment:
        return None
    start = document.find(fragment)
    if start < 0:
        return None
    return start, start + len(fragment)
