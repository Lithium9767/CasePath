from __future__ import annotations

from datetime import date

from pydantic import Field

from .base import Confidence, ContractModel, Identifier, SourceSpan
from .enums import ConditionStatus, DecisionStatus, MaturityLevel


class ClaimRecord(ContractModel):
    claim_id: Identifier
    claim_type: str = Field(min_length=1)
    claimant: str | None = None
    respondent: str | None = None
    requested_remedy: str = Field(min_length=1)
    amount: float | None = Field(default=None, ge=0)
    invoked_rule_ids: list[Identifier] = Field(default_factory=list)


class CourtFinding(ContractModel):
    finding_id: Identifier
    predicate: str = Field(min_length=1)
    polarity: bool | None = None
    source_span_ids: list[Identifier] = Field(min_length=1)


class ConditionFinding(ContractModel):
    condition_id: Identifier
    status: ConditionStatus
    finding_ids: list[Identifier] = Field(default_factory=list)
    confidence: Confidence
    source_span_ids: list[Identifier] = Field(default_factory=list)
    human_verified: bool = False


class ReasoningStep(ContractModel):
    reasoning_id: Identifier
    premise_finding_ids: list[Identifier] = Field(default_factory=list)
    applied_rule_ids: list[Identifier] = Field(default_factory=list)
    conclusion: str = Field(min_length=1)
    source_span_ids: list[Identifier] = Field(min_length=1)


class DecisionItem(ContractModel):
    decision_id: Identifier
    claim_id: Identifier
    status: DecisionStatus
    description: str = Field(min_length=1)
    amount: float | None = Field(default=None, ge=0)
    source_span_ids: list[Identifier] = Field(min_length=1)


class CaseRecord(ContractModel):
    contract_version: str = "1.0"
    case_id: Identifier
    title: str = Field(min_length=1)
    case_no: str | None = None
    court: str | None = None
    judgment_date: date | None = None
    cause: str | None = None
    maturity: MaturityLevel
    claims: list[ClaimRecord] = Field(min_length=1)
    findings: list[CourtFinding] = Field(default_factory=list)
    condition_findings: list[ConditionFinding] = Field(default_factory=list)
    reasoning_steps: list[ReasoningStep] = Field(default_factory=list)
    decisions: list[DecisionItem] = Field(default_factory=list)
    source_spans: list[SourceSpan] = Field(default_factory=list)
