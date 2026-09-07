"""Two-stage claim classification before any deep template is applied."""

from __future__ import annotations

import re
from typing import Any

from .ids import CLAIM_TYPE_GENERIC, CLAIM_TYPE_SERVICE_TERMINATION_REFUND

_TERMINATION = re.compile(r"解除")
_REFUND = re.compile(r"返还|退还|退款")
_PROPERTY_DAMAGE = re.compile(r"树木|车辆|维修费|倾倒|砸")
_SERVICE_CAUSE = re.compile(r"服务合同")
_PROPERTY_CAUSE = re.compile(r"物业")


def classify_claim_type(case: dict[str, Any], plaintiff_text: str) -> str:
    """Return a coarse claim type. Deep templates must check this first."""

    if is_service_termination_refund(case, plaintiff_text):
        return CLAIM_TYPE_SERVICE_TERMINATION_REFUND
    cause = str(case.get("case_cause") or "").strip()
    return cause or CLAIM_TYPE_GENERIC


def is_property_damages_claim(case: dict[str, Any], plaintiff_text: str) -> bool:
    title = str(case.get("title") or "")
    cause = str(case.get("case_cause") or "")
    haystack = f"{title}\n{cause}\n{plaintiff_text}"
    if not _PROPERTY_CAUSE.search(haystack):
        return False
    if _TERMINATION.search(plaintiff_text) and _REFUND.search(plaintiff_text):
        return False
    return bool(_PROPERTY_DAMAGE.search(haystack) or "赔偿" in plaintiff_text)


def is_service_contract_dispute(case: dict[str, Any], plaintiff_text: str) -> bool:
    """True for 服务合同纠纷, excluding 物业服务 and property-damage claims."""

    if is_property_damages_claim(case, plaintiff_text):
        return False
    title = str(case.get("title") or "")
    cause = str(case.get("case_cause") or "")
    if _PROPERTY_CAUSE.search(cause):
        return False
    return bool(_SERVICE_CAUSE.search(cause) or _SERVICE_CAUSE.search(title))


def is_service_termination_refund(case: dict[str, Any], plaintiff_text: str) -> bool:
    """True only for service-contract termination plus unperformed-fee refund."""

    if not is_service_contract_dispute(case, plaintiff_text):
        return False
    return bool(_TERMINATION.search(plaintiff_text) and _REFUND.search(plaintiff_text))
