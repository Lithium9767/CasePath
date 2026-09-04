from __future__ import annotations

from datetime import date

from pydantic import Field

from .base import ContractModel, Identifier, SourceSpan
from .enums import ConditionOperator, MaturityLevel


class ProvisionRef(ContractModel):
    source_id: Identifier
    provision_id: Identifier
    article_no: str
    title: str
    valid_from: date | None = None
    valid_to: date | None = None


class RuleCondition(ContractModel):
    condition_id: Identifier
    label: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    operator: ConditionOperator = ConditionOperator.ALL
    required: bool = True
    user_answerable: bool = True
    evidence_types: list[str] = Field(default_factory=list)
    source_span_ids: list[Identifier] = Field(default_factory=list)


class RuleException(ContractModel):
    exception_id: Identifier
    label: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    effect: str = Field(min_length=1)
    source_span_ids: list[Identifier] = Field(default_factory=list)


class LegalConsequence(ContractModel):
    consequence_id: Identifier
    consequence_type: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source_span_ids: list[Identifier] = Field(default_factory=list)


class RuleRecord(ContractModel):
    contract_version: str = "1.0"
    rule_id: Identifier
    title: str = Field(min_length=1)
    claim_types: list[str] = Field(min_length=1)
    provisions: list[ProvisionRef] = Field(min_length=1)
    conditions: list[RuleCondition] = Field(default_factory=list)
    exceptions: list[RuleException] = Field(default_factory=list)
    consequences: list[LegalConsequence] = Field(default_factory=list)
    maturity: MaturityLevel
    source_spans: list[SourceSpan] = Field(default_factory=list)
