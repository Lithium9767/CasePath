"""Curated refinements for the service-contract refund comparison set.

Only these cases receive refund-rule condition bridges. Other deep cases keep
claims/findings/decisions but do not get the six-condition template.
Machine output stays L1 / human_verified=false; overlays only fix extractable
evidence that heuristics miss. Holdings and APPLIES are never invented.
"""

from __future__ import annotations

from dataclasses import dataclass

from casepath.contracts import (
    CaseRecord,
    ClaimRecord,
    ConditionFinding,
    ConditionStatus,
    CourtFinding,
    ReasoningStep,
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

from .extract import SpanBook, applied_rule_ids_from_reasoning
from .ids import (
    CLAIM_TYPE_SERVICE_TERMINATION_REFUND,
    GYM_CASE_ID,
    LEXREL_CASE_ID_PREFIX,
)

# Prepaid service closed / unperformed / refund-shaped comparison set.
SERVICE_REFUND_COMPARISON_CASE_IDS: frozenset[str] = frozenset(
    {
        GYM_CASE_ID,
        f"{LEXREL_CASE_ID_PREFIX}102f10d1-ca09-4ed1-ae2e-f45007ce3038",  # 青鸟闭店
        f"{LEXREL_CASE_ID_PREFIX}1ec928ab-bb1e-4131-bafc-e17593454e1b",  # 培训关门退费
        f"{LEXREL_CASE_ID_PREFIX}829d6968-3b55-43c8-9e18-9d6ccccfd391",  # 携程部分退款
        f"{LEXREL_CASE_ID_PREFIX}8a246ddf-5387-4d25-b4fb-cd1847d2c66b",  # 易游旅游完成/保证金
    }
)

# Cases that invoke the gym refund rule (解除+返还 or prepaid closure+退还).
SERVICE_REFUND_RULE_CASE_IDS: frozenset[str] = frozenset(
    {
        GYM_CASE_ID,
        f"{LEXREL_CASE_ID_PREFIX}102f10d1-ca09-4ed1-ae2e-f45007ce3038",
        f"{LEXREL_CASE_ID_PREFIX}1ec928ab-bb1e-4131-bafc-e17593454e1b",
    }
)


@dataclass(frozen=True)
class ConditionOverlay:
    condition_id: str
    status: ConditionStatus
    quote: str
    confidence: float
    section: str = "基本案情"


@dataclass(frozen=True)
class ClaimOverlay:
    claim_type: str
    requested_remedy: str
    amount: float | None
    invoked_rule_ids: tuple[str, ...]


@dataclass(frozen=True)
class CaseOverlay:
    conditions: tuple[ConditionOverlay, ...]
    claim: ClaimOverlay | None = None
    # Keep machine reasoning when present; only gym has an anchored APPLIES path.
    keep_machine_reasoning: bool = True


def _unknown(condition_id: str) -> ConditionOverlay:
    return ConditionOverlay(
        condition_id=condition_id,
        status=ConditionStatus.UNKNOWN,
        quote="",
        confidence=0.3,
    )


_REFUND_CONDITION_ORDER = (
    COND_CONTRACT_EXISTS,
    COND_PAYMENT_MADE,
    COND_PERFORMANCE_IMPOSSIBLE,
    COND_UNPERFORMED_BALANCE,
    COND_ALTERNATIVE_PERFORMANCE,
    COND_CONTRACT_TERMINATED,
)


CASE_OVERLAYS: dict[str, CaseOverlay] = {
    GYM_CASE_ID: CaseOverlay(
        # Curated: do not treat "约定购买" as payment, or consequential "应予返还" as
        # direct proof of an unconsumed balance in the fact findings.
        conditions=(
            ConditionOverlay(
                COND_CONTRACT_EXISTS,
                ConditionStatus.SATISFIED,
                "2019年，曾某与某健身管理公司签订了《会员协议》，约定曾某购买某健身管理公司的会员会籍服务以及私人教练服务，成为某健身管理公司硚口店专属会员，期限为两年。",
                0.75,
            ),
            ConditionOverlay(
                COND_PAYMENT_MADE,
                ConditionStatus.SATISFIED,
                "根据《中华人民共和国民法典》第五百六十六条规定，被告应予返还原告交纳的会籍费用及不能提供服务的私人教练课程费用。",
                0.55,
                section="裁判理由",
            ),
            ConditionOverlay(
                COND_PERFORMANCE_IMPOSSIBLE,
                ConditionStatus.SATISFIED,
                "后某健身管理公司因自身经营原因，名下所有门店相继关闭，在对会员进行安置过程中，未经曾某同意，而将其会员会籍服务及私教服务转让给另一健身会所。",
                0.75,
            ),
            # 查明未直接写明“剩余/未消费”；返还金额来自裁判结果，不在此冒充事实认定。
            _unknown(COND_UNPERFORMED_BALANCE),
            ConditionOverlay(
                COND_ALTERNATIVE_PERFORMANCE,
                ConditionStatus.NOT_SATISFIED,
                "该健身会所距离曾某路途较远，不便于曾某日常锻炼。",
                0.65,
            ),
            ConditionOverlay(
                COND_CONTRACT_TERMINATED,
                ConditionStatus.SATISFIED,
                "一、解除曾某与某健身管理公司之间签订的《会员协议》；二、某健身管理公司于判决生效之日起十日内向原告曾某返还会籍费用以及私人教练课程费16370元。判决作出后各方均未提出上诉，一审判决已生效。",
                0.7,
            ),
        ),
    ),
    f"{LEXREL_CASE_ID_PREFIX}102f10d1-ca09-4ed1-ae2e-f45007ce3038": CaseOverlay(
        claim=ClaimOverlay(
            claim_type=CLAIM_TYPE_SERVICE_TERMINATION_REFUND,
            requested_remedy="因会所未正式开业且处于关闭状态，解除会员合同并返还已付会员费",
            amount=2499.0,
            invoked_rule_ids=(RULE_SERVICE_TERMINATION_REFUND,),
        ),
        conditions=(
            ConditionOverlay(
                COND_CONTRACT_EXISTS,
                ConditionStatus.SATISFIED,
                "原告张明政与被告青鸟公司签订青鸟国际健身俱乐部会员合同",
                0.75,
            ),
            ConditionOverlay(
                COND_PAYMENT_MADE,
                ConditionStatus.SATISFIED,
                "同日，被告青鸟公司向原告张明政出具青鸟国际健身有限公司专用收据一份，载明会员姓名为张明政，会员期为2017年8月31日至2022年8月30日，金额为2499元，并备注“包1个、次卡10张”",
                0.8,
            ),
            ConditionOverlay(
                COND_PERFORMANCE_IMPOSSIBLE,
                ConditionStatus.SATISFIED,
                "一直未正式开业，且现在处于关闭状态",
                0.8,
            ),
            ConditionOverlay(
                COND_UNPERFORMED_BALANCE,
                ConditionStatus.SATISFIED,
                "一直未正式开业，且现在处于关闭状态",
                0.7,
            ),
            _unknown(COND_ALTERNATIVE_PERFORMANCE),
            _unknown(COND_CONTRACT_TERMINATED),
        ),
    ),
    f"{LEXREL_CASE_ID_PREFIX}1ec928ab-bb1e-4131-bafc-e17593454e1b": CaseOverlay(
        claim=ClaimOverlay(
            claim_type=CLAIM_TYPE_SERVICE_TERMINATION_REFUND,
            requested_remedy="因培训机构关门停业，退还尚未上课的教育辅导培训费1200元",
            amount=1200.0,
            invoked_rule_ids=(RULE_SERVICE_TERMINATION_REFUND,),
        ),
        conditions=(
            ConditionOverlay(
                COND_CONTRACT_EXISTS,
                ConditionStatus.SATISFIED,
                "原告的法定监护人与被告武汉三特教育发展有限公司双方口头约定：原告在被告处接受教育辅导培训",
                0.7,
            ),
            ConditionOverlay(
                COND_PAYMENT_MADE,
                ConditionStatus.SATISFIED,
                "原告的法定监护人按约定向被告武汉三特教育发展有限公司缴纳了5，062元的教育辅导培训费",
                0.8,
            ),
            ConditionOverlay(
                COND_PERFORMANCE_IMPOSSIBLE,
                ConditionStatus.SATISFIED,
                "因被告武汉三特教育发展有限公司无故关门停业且未予退款确给原告柳芊芊造成一定的经济损失",
                0.8,
            ),
            ConditionOverlay(
                COND_UNPERFORMED_BALANCE,
                ConditionStatus.SATISFIED,
                "原告柳芊芊在被告武汉三特教育发展有限公司处尚有10次课（平均112.5元／每次）未开始上课",
                0.8,
            ),
            _unknown(COND_ALTERNATIVE_PERFORMANCE),
            _unknown(COND_CONTRACT_TERMINATED),
        ),
    ),
    f"{LEXREL_CASE_ID_PREFIX}829d6968-3b55-43c8-9e18-9d6ccccfd391": CaseOverlay(
        # Boundary: prepaid travel, partial refund already paid; no 解除, do not invoke rule.
        claim=ClaimOverlay(
            claim_type="服务合同纠纷",
            requested_remedy="请求退还因未履行服务责任而应当退还的剩余服务款17541元",
            amount=17541.0,
            invoked_rule_ids=(),
        ),
        conditions=(
            ConditionOverlay(
                COND_CONTRACT_EXISTS,
                ConditionStatus.SATISFIED,
                "原告马爽通过网络预订被告上海携程国际旅行社有限公司的",
                0.7,
            ),
            ConditionOverlay(
                COND_PAYMENT_MADE,
                ConditionStatus.SATISFIED,
                "原告马爽于当日支付了三人旅游费58，470元",
                0.8,
            ),
            ConditionOverlay(
                COND_PERFORMANCE_IMPOSSIBLE,
                ConditionStatus.UNKNOWN,
                "",
                0.3,
            ),
            # “尚有余额未退回”是争议退款余额，不能直接等同于已确认的未消费服务余额。
            ConditionOverlay(
                COND_UNPERFORMED_BALANCE,
                ConditionStatus.UNKNOWN,
                "",
                0.3,
            ),
            _unknown(COND_ALTERNATIVE_PERFORMANCE),
            _unknown(COND_CONTRACT_TERMINATED),
        ),
    ),
    f"{LEXREL_CASE_ID_PREFIX}8a246ddf-5387-4d25-b4fb-cd1847d2c66b": CaseOverlay(
        # Boundary: tour completed; dispute is deposit return, not unperformed membership.
        claim=ClaimOverlay(
            claim_type="服务合同纠纷",
            requested_remedy="请求退还按期归国保证金及相关服务费用",
            amount=60000.0,
            invoked_rule_ids=(),
        ),
        conditions=(
            ConditionOverlay(
                COND_CONTRACT_EXISTS,
                ConditionStatus.SATISFIED,
                "原告王洪珍代表三原告与被告易游天津分公司订立《团队出境旅游合同》",
                0.75,
            ),
            ConditionOverlay(
                COND_PAYMENT_MADE,
                ConditionStatus.SATISFIED,
                "当日原告按约定支付了团费，被告易游天津分公司出具了收据",
                0.75,
            ),
            ConditionOverlay(
                COND_PERFORMANCE_IMPOSSIBLE,
                ConditionStatus.NOT_SATISFIED,
                "三原告交纳上述费用后，按期参加了被告易游天津分公司组织的赴韩国旅游活动，并如期归国",
                0.75,
            ),
            ConditionOverlay(
                COND_UNPERFORMED_BALANCE,
                ConditionStatus.NOT_SATISFIED,
                "三原告交纳上述费用后，按期参加了被告易游天津分公司组织的赴韩国旅游活动，并如期归国",
                0.65,
            ),
            _unknown(COND_ALTERNATIVE_PERFORMANCE),
            _unknown(COND_CONTRACT_TERMINATED),
        ),
    ),
}


def uses_refund_condition_template(case_id: str) -> bool:
    return case_id in SERVICE_REFUND_COMPARISON_CASE_IDS


def _document_for(section: str, facts_text: str, reasoning_text: str) -> str:
    if section == "裁判理由":
        return reasoning_text
    return facts_text


def _finding_ids_for_quote(
    quote: str,
    findings: list[CourtFinding],
) -> list[str]:
    return [
        item.finding_id
        for item in findings
        if quote and quote in item.predicate
    ]


def _build_condition_findings(
    case_id: str,
    overlays: tuple[ConditionOverlay, ...],
    findings: list[CourtFinding],
    facts_text: str,
    reasoning_text: str,
    book: SpanBook,
) -> list[ConditionFinding]:
    by_id = {item.condition_id: item for item in overlays}
    ordered: list[ConditionFinding] = []
    for condition_id in _REFUND_CONDITION_ORDER:
        item = by_id.get(condition_id) or _unknown(condition_id)
        if not item.quote.strip():
            ordered.append(
                ConditionFinding(
                    condition_id=item.condition_id,
                    status=item.status,
                    finding_ids=[],
                    confidence=item.confidence,
                    source_span_ids=[],
                    human_verified=False,
                )
            )
            continue
        document = _document_for(item.section, facts_text, reasoning_text)
        if item.quote not in document:
            raise ValueError(
                f"refinement quote for {case_id}/{item.condition_id} not in {item.section}"
            )
        span = book.add(item.section, document, item.quote)
        ordered.append(
            ConditionFinding(
                condition_id=item.condition_id,
                status=item.status,
                finding_ids=_finding_ids_for_quote(item.quote, findings),
                confidence=item.confidence,
                source_span_ids=[span.span_id],
                human_verified=False,
            )
        )
    return ordered


def _apply_claim_overlay(record: CaseRecord, overlay: ClaimOverlay) -> list[ClaimRecord]:
    claim = record.claims[0]
    return [
        ClaimRecord(
            claim_id=claim.claim_id,
            claim_type=overlay.claim_type,
            claimant=claim.claimant,
            respondent=claim.respondent,
            requested_remedy=overlay.requested_remedy,
            amount=overlay.amount,
            invoked_rule_ids=list(overlay.invoked_rule_ids),
        )
    ]


def apply_case_overlay(
    record: CaseRecord,
    facts_text: str,
    reasoning_text: str,
) -> CaseRecord:
    """Apply curated comparison-set overlays, or strip refund conditions otherwise."""

    if record.case_id not in SERVICE_REFUND_COMPARISON_CASE_IDS:
        return record.model_copy(update={"condition_findings": []})

    overlay = CASE_OVERLAYS.get(record.case_id)
    if overlay is None or not overlay.conditions:
        return record

    # Rebuild spans: keep finding/decision/reasoning spans, replace condition spans.
    kept_span_ids = {
        span_id
        for item in record.findings
        for span_id in item.source_span_ids
    }
    kept_span_ids.update(
        span_id for step in record.reasoning_steps for span_id in step.source_span_ids
    )
    kept_span_ids.update(
        span_id for decision in record.decisions for span_id in decision.source_span_ids
    )
    book = SpanBook(record.case_id)
    for span in record.source_spans:
        if span.span_id in kept_span_ids:
            book.spans.append(span)

    claims = (
        _apply_claim_overlay(record, overlay.claim)
        if overlay.claim is not None
        else record.claims
    )
    condition_findings = _build_condition_findings(
        record.case_id,
        overlay.conditions,
        record.findings,
        facts_text,
        reasoning_text,
        book,
    )
    reasoning_steps: list[ReasoningStep] = list(record.reasoning_steps)
    if reasoning_steps and overlay.keep_machine_reasoning:
        # 每个步骤仅依据自身已锚定的说理重算；保留全部步骤，不复制请求关系。
        reasoning_steps = [
            ReasoningStep(
                reasoning_id=step.reasoning_id,
                premise_finding_ids=step.premise_finding_ids,
                applied_rule_ids=(
                    applied_rule_ids_from_reasoning(step.conclusion)
                    if applied_rule_ids_from_reasoning(reasoning_text) else []
                ),
                conclusion=step.conclusion,
                source_span_ids=step.source_span_ids,
            )
            for step in reasoning_steps
        ]
    elif not overlay.keep_machine_reasoning:
        reasoning_steps = []

    return CaseRecord(
        case_id=record.case_id,
        title=record.title,
        case_no=record.case_no,
        court=record.court,
        judgment_date=record.judgment_date,
        cause=record.cause,
        maturity=record.maturity,
        claims=claims,
        findings=record.findings,
        condition_findings=condition_findings,
        reasoning_steps=reasoning_steps,
        decisions=record.decisions,
        source_spans=book.spans,
    )
