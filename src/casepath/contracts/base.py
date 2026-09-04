from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class ContractModel(BaseModel):
    """Base class for all values exchanged across team-owned modules."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class SourceSpan(ContractModel):
    span_id: Identifier
    source_id: Identifier
    section: str | None = None
    paragraph_id: str | None = None
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    quote: str = Field(min_length=1)
    content_hash: str | None = None

    @model_validator(mode="after")
    def validate_offsets(self) -> SourceSpan:
        if self.end_offset < self.start_offset:
            raise ValueError("end_offset must be greater than or equal to start_offset")
        return self


class ScoreComponent(ContractModel):
    name: Identifier
    value: float
    explanation: str
