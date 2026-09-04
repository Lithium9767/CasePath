from __future__ import annotations

from datetime import UTC, datetime

from pydantic import Field

from .base import ContractModel, Identifier, ScoreComponent
from .enums import ConditionStatus, SessionStatus


class UserFact(ContractModel):
    fact_id: Identifier
    text: str = Field(min_length=1)
    predicate: str | None = None
    value: str | bool | float | None = None
    source_turn: int = Field(ge=0)


class CandidateClaim(ContractModel):
    claim_type: str = Field(min_length=1)
    requested_remedy: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class QueryConditionState(ContractModel):
    condition_id: Identifier
    status: ConditionStatus = ConditionStatus.UNKNOWN
    supporting_fact_ids: list[Identifier] = Field(default_factory=list)
    last_updated_turn: int = Field(default=0, ge=0)


class DialogueTurn(ContractModel):
    turn_id: int = Field(ge=1)
    condition_id: Identifier
    question: str = Field(min_length=1)
    answer: str | None = None


class QueryState(ContractModel):
    contract_version: str = "1.0"
    session_id: Identifier
    initial_query: str = Field(min_length=1)
    status: SessionStatus = SessionStatus.INITIAL
    user_facts: list[UserFact] = Field(default_factory=list)
    candidate_claims: list[CandidateClaim] = Field(default_factory=list)
    condition_states: list[QueryConditionState] = Field(default_factory=list)
    dialogue_history: list[DialogueTurn] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class QuestionCandidate(ContractModel):
    question_id: Identifier
    condition_id: Identifier
    question: str = Field(min_length=1)
    why_asked: str = Field(min_length=1)
    options: list[str] = Field(min_length=2)
    utility: float
    score_components: list[ScoreComponent] = Field(default_factory=list)
    supporting_case_ids: list[Identifier] = Field(default_factory=list)
    limiting_case_ids: list[Identifier] = Field(default_factory=list)
