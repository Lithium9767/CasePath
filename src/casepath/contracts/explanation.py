from __future__ import annotations

from pydantic import Field

from .base import ContractModel, Identifier


class CitationRecord(ContractModel):
    citation_id: Identifier
    source_span_ids: list[Identifier] = Field(min_length=1)
    supports: str = Field(min_length=1)
    verified: bool = False


class ExplanationBranch(ContractModel):
    branch_id: Identifier
    condition: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    citation_ids: list[Identifier] = Field(default_factory=list)


class EvidenceAction(ContractModel):
    action_id: Identifier
    description: str = Field(min_length=1)
    related_condition_ids: list[Identifier] = Field(default_factory=list)


class ExplanationPlan(ContractModel):
    contract_version: str = "1.0"
    session_id: Identifier
    main_explanation: str = Field(min_length=1)
    candidate_claims: list[str] = Field(default_factory=list)
    applicable_rule_ids: list[Identifier] = Field(default_factory=list)
    support_case_ids: list[Identifier] = Field(default_factory=list)
    limiting_case_ids: list[Identifier] = Field(default_factory=list)
    conditional_branches: list[ExplanationBranch] = Field(default_factory=list)
    unresolved_condition_ids: list[Identifier] = Field(default_factory=list)
    evidence_actions: list[EvidenceAction] = Field(default_factory=list)
    citations: list[CitationRecord] = Field(default_factory=list)
    disclaimer: str = "该解释基于当前提供的信息，不替代律师针对完整材料出具的法律意见。"
