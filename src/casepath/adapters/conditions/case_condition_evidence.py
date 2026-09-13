"""C/D 共用的案例条件认定聚合。禁止各取第一条或简单多数投票掩盖冲突。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from casepath.adapters.retrieval.retrieval_catalog import CaseAssessment
from casepath.contracts import ConditionStatus


@dataclass(frozen=True)
class CaseConditionSummary:
    status: ConditionStatus
    evidence_ids: tuple[str, ...]
    has_usable_evidence: bool
    conflicting: bool
    support_count: int
    oppose_count: int


def aggregate_case_condition(
    *,
    case_id: str,
    condition_id: str,
    assessments: Sequence[CaseAssessment],
) -> CaseConditionSummary:
    """聚合同一案例同一条件的可用认定。

    仅接受带 span/finding 依据的条目；同一证据身份去重；支持与反对并存则 CONFLICTING。
    """
    del case_id  # 保留签名便于日志/调用方统一
    statuses: list[ConditionStatus] = []
    evidence_ids: list[str] = []
    seen_evidence: set[str] = set()
    for item in assessments:
        if item.condition_id != condition_id:
            continue
        if not (item.span_ids or item.finding_ids):
            continue
        identity = (
            item.status.value,
            tuple(item.span_ids),
            tuple(item.finding_ids),
        )
        key = repr(identity)
        if key in seen_evidence:
            continue
        seen_evidence.add(key)
        statuses.append(item.status)
        evidence_ids.extend(item.span_ids)
        evidence_ids.extend(item.finding_ids)
    if not statuses:
        return CaseConditionSummary(
            status=ConditionStatus.UNKNOWN,
            evidence_ids=(),
            has_usable_evidence=False,
            conflicting=False,
            support_count=0,
            oppose_count=0,
        )
    unique = set(statuses)
    support_count = sum(1 for status in statuses if status is ConditionStatus.SATISFIED)
    oppose_count = sum(1 for status in statuses if status is ConditionStatus.NOT_SATISFIED)
    if ConditionStatus.CONFLICTING in unique or (
        ConditionStatus.SATISFIED in unique and ConditionStatus.NOT_SATISFIED in unique
    ):
        return CaseConditionSummary(
            status=ConditionStatus.CONFLICTING,
            evidence_ids=tuple(dict.fromkeys(evidence_ids)),
            has_usable_evidence=True,
            conflicting=True,
            support_count=support_count,
            oppose_count=oppose_count,
        )
    if len(unique) == 1:
        status = next(iter(unique))
        return CaseConditionSummary(
            status=status,
            evidence_ids=tuple(dict.fromkeys(evidence_ids)),
            has_usable_evidence=status
            in {
                ConditionStatus.SATISFIED,
                ConditionStatus.NOT_SATISFIED,
                ConditionStatus.CONFLICTING,
            },
            conflicting=False,
            support_count=support_count,
            oppose_count=oppose_count,
        )
    return CaseConditionSummary(
        status=ConditionStatus.UNKNOWN,
        evidence_ids=tuple(dict.fromkeys(evidence_ids)),
        has_usable_evidence=False,
        conflicting=False,
        support_count=support_count,
        oppose_count=oppose_count,
    )
