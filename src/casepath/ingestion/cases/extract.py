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
    parse_granted_amount,
    parse_requested_amount,
)

_CONTRACT_RE = re.compile(r"签订.{0,20}(协议|合同)")
_SERVICE_PAYMENT_RE = re.compile(
    r"(购买|支付|交纳|付款).{0,24}(会籍|私教|会员|服务费|课程|服务)"
)
_REPAIR_FEE_RE = re.compile(r"维修费|停车管理费")
_IMPOSSIBLE_RE = re.compile(r"关闭|闭店|撤离|无法提供|不能提供|停止营业|相继关闭")
_STILL_OPEN_RE = re.compile(r"正常营业|继续提供|仍可提供")
_UNPERFORMED_RE = re.compile(r"剩余|未消费|未履行|不能提供服务的|未使用")
_ALTERNATIVE_RE = re.compile(r"转让|安置|另一.{0,8}(会所|门店)|替代")
_ALTERNATIVE_FAIL_RE = re.compile(r"距离较远|不便于|未经.{0,8}同意")
_TERMINATED_RE = re.compile(r"解除.{0,20}(协议|合同)")
_REJECT_ALL_RE = re.compile(r"驳回原告.{0,20}诉讼请求")
_REJECT_OTHER_RE = re.compile(r"驳回.{0,8}其他诉讼请求")


@dataclass
class SpanBook:
    case_id: str
    spans: list[SourceSpan] = field(default_factory=list)

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
        span = SourceSpan(
            span_id=entity_id(self.case_id, "span", len(self.spans) + 1),
            source_id=f"case.{self.case_id}",
            section=section,
            paragraph_id=f"{section}-{len(self.spans) + 1:03d}",
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


def extract_reasoning_step(
    case_id: str,
    reasoning_text: str,
    findings: list[CourtFinding],
    invoked_rule_ids: list[str],
    book: SpanBook,
) -> ReasoningStep | None:
    sentence = _key_reasoning_sentence(reasoning_text)
    if not sentence:
        return None
    try:
        span = book.add("裁判理由", reasoning_text, sentence)
    except ValueError:
        return None
    return ReasoningStep(
        reasoning_id=entity_id(case_id, "reasoning", 1),
        premise_finding_ids=[item.finding_id for item in findings[:1]],
        applied_rule_ids=invoked_rule_ids,
        conclusion=sentence,
        source_span_ids=[span.span_id],
    )


def extract_decision(
    case_id: str,
    claim: ClaimRecord,
    partitions: FactPartitions,
    facts_text: str,
    reasoning_text: str,
    book: SpanBook,
) -> DecisionItem:
    holding = partitions.holding or ""
    document = facts_text if holding and holding in facts_text else reasoning_text
    section = "裁判结果" if holding and holding in facts_text else "裁判理由"
    quote = _holding_quote(holding) or _first_sentence(reasoning_text) or "裁判结果待核验"
    if quote not in document:
        document = quote
        section = "裁判结果"
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
        _payment_bridge(sentences, finding_by_predicate, book, facts_text),
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
        if _UNPERFORMED_RE.search(match.group(0)) or "不能提供服务" in match.group(0)
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
