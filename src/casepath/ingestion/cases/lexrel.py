"""Convert pinned LexRel civil-relation samples into CasePath L1/deep contracts."""

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
    EXPECTED_LEXREL_RETRIEVAL_CASE_COUNT,
    EXPECTED_LEXREL_SERVICE_CASE_COUNT,
    LEXREL_CASE_ID_PREFIX,
    REVIEWED_L2_CASE_IDS,
)
from .refinements import apply_case_overlay, uses_refund_condition_template
from .sections import parse_requested_amount

RELATION_TYPE = "服务合同关系"
CAUSE = "服务合同纠纷"
RELATION_CAUSES = {
    "借款合同关系": "借款合同纠纷",
    "保证合同关系": "保证合同纠纷",
    "夫妻关系": "婚姻家庭纠纷",
    "机动车交通事故侵权责任法律关系": "机动车交通事故责任纠纷",
    "财产保险合同关系": "财产保险合同纠纷",
    "除房屋外的买卖合同关系": "买卖合同纠纷",
    "抵押合同关系": "抵押合同纠纷",
    "房屋买卖合同关系": "房屋买卖合同纠纷",
    "追偿权关系": "追偿权纠纷",
    "劳务合同关系": "劳务合同纠纷",
    "租赁合同关系": "租赁合同纠纷",
    "物业服务合同关系": "物业服务合同纠纷",
    RELATION_TYPE: CAUSE,
}


def case_id_for(uniqid: str) -> str:
    return f"{LEXREL_CASE_ID_PREFIX}{uniqid.strip()}"


def _first_sentence(text: str) -> str:
    match = re.search(r"[^。；\n]+[。；]?", text or "")
    return match.group(0).strip() if match else ""


def _requested_remedy(plaintiff: str) -> str:
    for pattern in (r"请求判令[：:]?([^。；\n]+)", r"主张([^。；\n]+)"):
        match = re.search(pattern, plaintiff or "")
        if match:
            return match.group(1).strip()[:240]
    return ""


def _first_content_line(content: str) -> str:
    for line in (content or "").splitlines():
        text = re.sub(r"^(权利|义务)[：:]\s*", "", line).strip()
        if text:
            return text[:240]
    return _first_sentence(content)


def _title(relation_type: str, uniqid: str) -> str:
    # LexRel only labels relation participants. It does not identify plaintiff/defendant roles.
    return f"LexRel{relation_type}案例 {uniqid}"


def _relation_payload(sample: dict[str, Any], relation_type: str) -> dict[str, Any]:
    relation = sample.get("relation") or {}
    items = relation.get(relation_type) or []
    if not items or not isinstance(items[0], dict):
        raise ValueError(f"sample {sample.get('uniqid')} has no {relation_type}")
    return items[0]


def convert_sample(
    sample: dict[str, Any],
    relation_type: str = RELATION_TYPE,
) -> tuple[CaseIndexRecord, CaseRecord]:
    uniqid = str(sample.get("uniqid") or "").strip()
    if not uniqid:
        raise ValueError("LexRel sample is missing uniqid")
    facts_text = str(sample.get("input") or "").strip()
    if not facts_text:
        raise ValueError(f"LexRel sample {uniqid} has empty input facts")
    if relation_type not in RELATION_CAUSES:
        raise ValueError(f"unsupported LexRel relation type: {relation_type}")
    payload = _relation_payload(sample, relation_type)
    relation = sample.get("relation") or {}
    relation_labels = [relation_type, *sorted(key for key in relation if key != relation_type)]
    object_ = str(payload.get("客体") or "")
    content = str(payload.get("内容") or "")
    case_id = case_id_for(uniqid)
    cause = RELATION_CAUSES[relation_type]
    title = _title(relation_type, uniqid)
    pleading = "\n".join(part for part in (content, object_, facts_text) if part)
    case = {"title": title, "case_cause": cause}
    refund = relation_type == RELATION_TYPE and is_service_termination_refund(case, pleading)
    explicit_remedy = _requested_remedy(facts_text) if refund else ""
    index = CaseIndexRecord(
        case_id=case_id,
        title=title,
        case_no=None,
        court=None,
        judgment_date=None,
        cause=cause,
        keywords=[*relation_labels, "LexRel", "民事案例"],
        facts_text=facts_text,
        reasoning_text="",
        summary=(object_ or _first_content_line(content) or cause)[:400],
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
        if relation_type == RELATION_TYPE and uses_refund_condition_template(case_id)
        else []
    )
    claim = ClaimRecord(
        claim_id=entity_id(case_id, "claim", 1),
        claim_type=CLAIM_TYPE_SERVICE_TERMINATION_REFUND if refund else cause,
        claimant=None,
        respondent=None,
        requested_remedy=(
            explicit_remedy
            if explicit_remedy
            else f"与{object_.strip() or relation_type}有关的民事请求（原数据未标注具体诉请）"
        )[:240],
        amount=parse_requested_amount(pleading),
        invoked_rule_ids=[RULE_SERVICE_TERMINATION_REFUND] if refund else [],
    )
    maturity = MaturityLevel.L2 if case_id in REVIEWED_L2_CASE_IDS else MaturityLevel.L1
    record = CaseRecord(
        case_id=case_id,
        title=title,
        case_no=None,
        court=None,
        judgment_date=None,
        cause=cause,
        maturity=maturity,
        claims=[claim],
        findings=findings,
        condition_findings=condition_findings,
        reasoning_steps=[],
        decisions=[],
        source_spans=book.spans,
    )
    if relation_type == RELATION_TYPE:
        record = apply_case_overlay(record, facts_text, "")
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


def load_retrieval_cases(path: Path) -> tuple[list[CaseIndexRecord], list[CaseRecord]]:
    """Load the balanced retrieval subset with an explicit primary relation per sample."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise TypeError("LexRel retrieval input must be a JSON array")
    lightweight: list[CaseIndexRecord] = []
    deep: list[CaseRecord] = []
    seen_ids: set[str] = set()
    seen_facts: set[str] = set()
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("sample"), dict):
            raise TypeError("LexRel retrieval item must contain a sample object")
        relation_type = str(item.get("primary_relation_type") or "").strip()
        sample = item["sample"]
        index, record = convert_sample(sample, relation_type)
        normalized_facts = re.sub(r"\s+", "", index.facts_text)
        if index.case_id in seen_ids:
            raise ValueError(f"duplicate LexRel case_id: {index.case_id}")
        if normalized_facts in seen_facts:
            raise ValueError(f"duplicate LexRel facts: {index.case_id}")
        seen_ids.add(index.case_id)
        seen_facts.add(normalized_facts)
        lightweight.append(index)
        deep.append(record)
    if len(lightweight) != EXPECTED_LEXREL_RETRIEVAL_CASE_COUNT:
        raise ValueError(
            "unexpected LexRel retrieval count: "
            f"expected {EXPECTED_LEXREL_RETRIEVAL_CASE_COUNT}, received {len(lightweight)}"
        )
    order = lambda item: item.case_id
    return sorted(lightweight, key=order), sorted(deep, key=order)
