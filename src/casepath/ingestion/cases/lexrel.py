"""Convert LexRel 服务合同关系 samples into CasePath L1 and deep contracts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from casepath.contracts import (
    CaseIndexRecord,
    CaseRecord,
    ClaimRecord,
    MaturityLevel,
)
from casepath.rule_layer.ids import RULE_SERVICE_TERMINATION_REFUND

from .citations import related_provision_ids_from_text
from .classify import is_service_termination_refund
from .extract import (
    SpanBook,
    entity_id,
    extract_condition_findings,
    extract_findings_from_facts,
)
from .ids import (
    CLAIM_TYPE_SERVICE_TERMINATION_REFUND,
    EXPECTED_LEXREL_SERVICE_CASE_COUNT,
    LEXREL_CASE_ID_PREFIX,
    REVIEWED_L2_CASE_IDS,
)
from .refinements import (
    SERVICE_REFUND_RULE_CASE_IDS,
    apply_case_overlay,
    uses_refund_condition_template,
)
from .sections import parse_requested_amount

RELATION_TYPE = "服务合同关系"
CAUSE = "服务合同纠纷"
UNANNOTATED_REMEDY = "原数据未标注具体诉请"
_REQUEST_RE = re.compile(r"请求(?:法院)?(?:依法)?(?:判令)?[：:]?|诉讼请求[：:]?|诉请|主张|要求")
_REMEDY_ACTION_RE = re.compile(
    r"返还|退还|退款|退团|退费|解除|支付|给付|赔偿|补偿|偿还|归还|履行|确认|撤销|停止|交付"
)


def case_id_for(uniqid: str) -> str:
    return f"{LEXREL_CASE_ID_PREFIX}{uniqid.strip()}"


def _first_sentence(text: str) -> str:
    match = re.search(r"[^。；\n]+[。；]?", text or "")
    return match.group(0).strip() if match else ""


def _requested_remedy(facts_text: str) -> str:
    """Keep an expressed request, never an annotated right or a contract price."""

    for sentence in re.findall(r"[^。\n]+", facts_text):
        for match in _REQUEST_RE.finditer(sentence):
            prefix = sentence[: match.start()]
            # A permission/contract term is not a request that was actually made.
            if re.search(
                r"有权(?:利)?$|无权$|可以$|可$|不得$|不再$|未曾$|没有$|无需$|无须$|未$|不$",
                prefix,
            ):
                continue
            if re.search(r"(?:合同|协议)(?:中)?(?:约定|载明)", prefix):
                continue
            remainder = sentence[match.end() :].strip()
            if _REMEDY_ACTION_RE.search(remainder):
                return sentence[match.start() :].strip()
    return ""


def _first_content_line(content: str) -> str:
    for line in (content or "").splitlines():
        text = re.sub(r"^(权利|义务)[：:]\s*", "", line).strip()
        if text:
            return text[:240]
    return _first_sentence(content)


def _title(uniqid: str) -> str:
    # Relation participants have no plaintiff/defendant role or ordering contract.
    return f"LexRel服务合同关系案例 {uniqid}"


def _relation_payload(sample: dict[str, Any]) -> dict[str, Any]:
    relation = sample.get("relation") or {}
    items = relation.get(RELATION_TYPE) or []
    if not items or not isinstance(items[0], dict):
        raise ValueError(f"sample {sample.get('uniqid')} has no {RELATION_TYPE}")
    return items[0]


def convert_sample(sample: dict[str, Any]) -> tuple[CaseIndexRecord, CaseRecord]:
    uniqid = str(sample.get("uniqid") or "").strip()
    if not uniqid:
        raise ValueError("LexRel sample is missing uniqid")
    facts_text = str(sample.get("input") or "").strip()
    if not facts_text:
        raise ValueError(f"LexRel sample {uniqid} has empty input facts")
    payload = _relation_payload(sample)
    object_ = str(payload.get("客体") or "")
    content = str(payload.get("内容") or "")
    case_id = case_id_for(uniqid)
    title = _title(uniqid)
    explicit_remedy = _requested_remedy(facts_text)
    case = {"title": title, "case_cause": CAUSE}
    refund = bool(explicit_remedy) and (
        is_service_termination_refund(case, explicit_remedy)
        or (
            case_id in SERVICE_REFUND_RULE_CASE_IDS
            and bool(re.search(r"返还|退还|退款|退费", explicit_remedy))
        )
    )
    index = CaseIndexRecord(
        case_id=case_id,
        title=title,
        case_no=None,
        court=None,
        judgment_date=None,
        cause=CAUSE,
        keywords=[RELATION_TYPE, "LexRel"],
        facts_text=facts_text,
        reasoning_text="",
        summary=(object_ or _first_content_line(content) or CAUSE)[:400],
        related_provision_ids=related_provision_ids_from_text(facts_text, content, object_),
        maturity=MaturityLevel.L1,
    )
    book = SpanBook(case_id)
    findings = extract_findings_from_facts(case_id, facts_text, book)
    condition_findings = (
        extract_condition_findings(
            findings,
            facts_text,
            "",
            "",
            book,
        )
        if uses_refund_condition_template(case_id)
        else []
    )
    claim = ClaimRecord(
        claim_id=entity_id(case_id, "claim", 1),
        claim_type=CLAIM_TYPE_SERVICE_TERMINATION_REFUND if refund else CAUSE,
        claimant=None,
        respondent=None,
        requested_remedy=explicit_remedy or UNANNOTATED_REMEDY,
        amount=parse_requested_amount(explicit_remedy),
        invoked_rule_ids=[RULE_SERVICE_TERMINATION_REFUND] if refund else [],
    )
    maturity = MaturityLevel.L2 if case_id in REVIEWED_L2_CASE_IDS else MaturityLevel.L1
    record = CaseRecord(
        case_id=case_id,
        title=title,
        case_no=None,
        court=None,
        judgment_date=None,
        cause=CAUSE,
        maturity=maturity,
        claims=[claim],
        findings=findings,
        condition_findings=condition_findings,
        reasoning_steps=[],
        decisions=[],
        source_spans=book.spans,
    )
    record = apply_case_overlay(record, facts_text, "")
    # Keep the source-anchored condition bridges, but an overlay must not turn
    # a payment/balance or hypothetical remedy into the source's actual claim.
    record.claims = [claim]
    return index, record


def load_service_contract_cases(
    path: Path,
) -> tuple[list[CaseIndexRecord], list[CaseRecord]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise TypeError("LexRel service-contract input must be a JSON array")
    lightweight: list[CaseIndexRecord] = []
    deep: list[CaseRecord] = []
    seen: set[str] = set()
    for sample in payload:
        if not isinstance(sample, dict):
            raise TypeError("LexRel sample must be an object")
        relation = sample.get("relation") or {}
        if RELATION_TYPE not in relation:
            continue
        index, record = convert_sample(sample)
        if index.case_id in seen:
            raise ValueError(f"duplicate LexRel case_id: {index.case_id}")
        seen.add(index.case_id)
        lightweight.append(index)
        deep.append(record)
    if len(lightweight) != EXPECTED_LEXREL_SERVICE_CASE_COUNT:
        raise ValueError(
            "unexpected LexRel 服务合同关系 count: "
            f"expected {EXPECTED_LEXREL_SERVICE_CASE_COUNT}, received {len(lightweight)}"
        )
    order = lambda item: item.case_id
    return sorted(lightweight, key=order), sorted(deep, key=order)
