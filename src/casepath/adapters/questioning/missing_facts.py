"""Missing fact candidates for E questioning."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field

from casepath.contracts import ConditionStatus, ContractModel

MissingFactStatus = Literal[
    "MISSING",
    "ALREADY_ANSWERED",
    "USER_UNABLE",
    "USER_DECLINED",
    "UNCERTAIN",
]

_VALID_STATUSES: set[str] = {
    "MISSING",
    "ALREADY_ANSWERED",
    "USER_UNABLE",
    "USER_DECLINED",
    "UNCERTAIN",
}


class MissingFactCandidate(ContractModel):
    """Per-turn missing-fact candidate; candidate_id is program-assigned."""

    candidate_id: str
    condition_ids: list[str] = Field(min_length=1)
    description: str = Field(min_length=1, max_length=160)
    status: MissingFactStatus
    checked_fact_ids: list[str] = Field(default_factory=list)
    checked_question_ids: list[str] = Field(default_factory=list)
    why_needed: str = Field(min_length=1, max_length=200)
    priority_tier: int = 3
    rule_rank: int = 0
    rule_title: str = ""
    selection_why: str = ""


class MissingFactDraft(ContractModel):
    condition_ids: list[str] = Field(min_length=1)
    description: str = Field(default="", max_length=160)
    status: MissingFactStatus
    checked_fact_ids: list[str] = Field(default_factory=list)
    checked_question_ids: list[str] = Field(default_factory=list)
    why_needed: str = Field(default="needed for current explanation", max_length=200)


class MissingFactIdentifyBatch(ContractModel):
    items: list[MissingFactDraft] = Field(default_factory=list)


def effective_facts_for_questioning(state, meta) -> list[dict]:
    superseded: dict[str, set[str | None]] = {}
    if meta is not None:
        for item in meta.corrections:
            superseded.setdefault(item.target_fact_id, set()).add(item.condition_id)
    rows: list[dict] = []
    for fact in state.user_facts:
        scopes = superseded.get(fact.fact_id)
        if scopes is not None and None in scopes:
            continue
        if scopes:
            rows.append(
                {
                    "fact_id": fact.fact_id,
                    "text": fact.text,
                    "evidence_role": "partially_superseded",
                    "inactive_for_conditions": sorted(c for c in scopes if c),
                }
            )
        else:
            rows.append(
                {
                    "fact_id": fact.fact_id,
                    "text": fact.text,
                    "evidence_role": "effective",
                    "inactive_for_conditions": [],
                }
            )
    return rows


def merge_drafts_by_description(drafts: list[MissingFactDraft]) -> list[MissingFactDraft]:
    merged: dict[str, MissingFactDraft] = {}
    order: list[str] = []
    for draft in drafts:
        key = re.sub(r"\s+", "", draft.description.strip())
        if key not in merged:
            merged[key] = draft.model_copy(deep=True)
            order.append(key)
            continue
        current = merged[key]
        condition_ids = list(dict.fromkeys([*current.condition_ids, *draft.condition_ids]))
        fact_ids = list(dict.fromkeys([*current.checked_fact_ids, *draft.checked_fact_ids]))
        question_ids = list(
            dict.fromkeys([*current.checked_question_ids, *draft.checked_question_ids])
        )
        status_rank = {
            "ALREADY_ANSWERED": 0,
            "USER_UNABLE": 0,
            "USER_DECLINED": 0,
            "UNCERTAIN": 1,
            "MISSING": 2,
        }
        status = current.status
        if status_rank.get(draft.status, 9) < status_rank.get(status, 9):
            status = draft.status
        merged[key] = current.model_copy(
            update={
                "condition_ids": condition_ids,
                "checked_fact_ids": fact_ids,
                "checked_question_ids": question_ids,
                "status": status,
                "why_needed": current.why_needed or draft.why_needed,
            }
        )
    return [merged[key] for key in order]


def validate_missing_fact_drafts(
    drafts: list[MissingFactDraft],
    *,
    allowed_condition_ids: set[str],
    known_fact_ids: set[str],
    known_question_ids: set[str],
) -> tuple[list[MissingFactDraft], list[str]]:
    kept: list[MissingFactDraft] = []
    reasons: list[str] = []
    for index, draft in enumerate(drafts):
        if draft.status not in _VALID_STATUSES:
            reasons.append(f"item[{index}]:invalid_status")
            continue
        if not draft.description.strip():
            reasons.append(f"item[{index}]:empty_description")
            continue
        conditions = [cid for cid in draft.condition_ids if cid in allowed_condition_ids]
        if not conditions:
            reasons.append(f"item[{index}]:no_allowed_condition")
            continue
        if len(conditions) != len(draft.condition_ids):
            reasons.append(f"item[{index}]:dropped_unknown_condition")
        facts = [fid for fid in draft.checked_fact_ids if fid in known_fact_ids]
        questions = [qid for qid in draft.checked_question_ids if qid in known_question_ids]
        kept.append(
            draft.model_copy(
                update={
                    "condition_ids": list(dict.fromkeys(conditions)),
                    "checked_fact_ids": list(dict.fromkeys(facts)),
                    "checked_question_ids": list(dict.fromkeys(questions)),
                    "description": draft.description.strip(),
                    "why_needed": draft.why_needed.strip(),
                }
            )
        )
    return kept, reasons


def filter_missing_facts(
    candidates: list[MissingFactCandidate],
) -> tuple[list[MissingFactCandidate], dict[str, int]]:
    askable: list[MissingFactCandidate] = []
    excluded: dict[str, int] = {}
    for item in candidates:
        if item.status == "MISSING":
            askable.append(item)
            continue
        excluded[item.status] = excluded.get(item.status, 0) + 1
    return askable, excluded


def rank_missing_facts(candidates: list[MissingFactCandidate]) -> list[MissingFactCandidate]:
    return sorted(
        candidates,
        key=lambda item: (item.priority_tier, item.rule_rank, item.candidate_id),
    )


def assign_candidate_ids(
    drafts: list[MissingFactDraft],
    *,
    condition_meta: dict[str, tuple[int, int, str, str]],
) -> list[MissingFactCandidate]:
    out: list[MissingFactCandidate] = []
    for index, draft in enumerate(drafts, start=1):
        tiers = [condition_meta[cid][0] for cid in draft.condition_ids if cid in condition_meta]
        ranks = [condition_meta[cid][1] for cid in draft.condition_ids if cid in condition_meta]
        why = next(
            (condition_meta[cid][2] for cid in draft.condition_ids if cid in condition_meta),
            draft.why_needed,
        )
        title = next(
            (condition_meta[cid][3] for cid in draft.condition_ids if cid in condition_meta),
            "",
        )
        out.append(
            MissingFactCandidate(
                candidate_id=f"mf.{index}",
                condition_ids=list(draft.condition_ids),
                description=draft.description,
                status=draft.status,
                checked_fact_ids=list(draft.checked_fact_ids),
                checked_question_ids=list(draft.checked_question_ids),
                why_needed=draft.why_needed,
                priority_tier=min(tiers) if tiers else 3,
                rule_rank=min(ranks) if ranks else 99,
                rule_title=title,
                selection_why=why,
            )
        )
    return out


def condition_evidence_summary(state, condition_ids: list[str]) -> list[dict]:
    rows = []
    by_id = {item.condition_id: item for item in state.condition_states}
    for cid in condition_ids:
        item = by_id.get(cid)
        if item is None:
            rows.append(
                {
                    "condition_id": cid,
                    "status": ConditionStatus.UNKNOWN.value,
                    "evidence": [],
                }
            )
            continue
        rows.append(
            {
                "condition_id": cid,
                "status": item.status.value,
                "evidence": [
                    {"fact_id": evidence.fact_id, "relation": evidence.relation}
                    for evidence in item.evidence
                ],
            }
        )
    return rows


_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*元")


def question_bundles_known_amounts(question: str, fact_texts: list[str], description: str) -> bool:
    known: set[str] = set()
    for text in fact_texts:
        known.update(_AMOUNT.findall(text))
    if not known:
        return False
    asked = set(_AMOUNT.findall(question))
    if not asked:
        return False
    desc_amounts = set(_AMOUNT.findall(description))
    return bool((asked & known) - desc_amounts)
