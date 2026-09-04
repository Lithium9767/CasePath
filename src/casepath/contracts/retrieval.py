from __future__ import annotations

from pydantic import Field

from .base import ContractModel, Identifier


class ScoredReference(ContractModel):
    object_id: Identifier
    score: float
    reasons: list[str] = Field(default_factory=list)


class RetrievalBundle(ContractModel):
    contract_version: str = "1.0"
    rule_refs: list[ScoredReference] = Field(default_factory=list)
    support_case_refs: list[ScoredReference] = Field(default_factory=list)
    limiting_case_refs: list[ScoredReference] = Field(default_factory=list)
    boundary_case_refs: list[ScoredReference] = Field(default_factory=list)
    cited_span_ids: list[Identifier] = Field(default_factory=list)
    degraded: bool = False
    degradation_reason: str | None = None
