from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, Field, StringConstraints, model_validator

from .base import ContractModel, Identifier
from .enums import MaturityLevel

ContentHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
EffectiveStatus = Literal["effective", "repealed", "not_yet_effective"]
ArticleNumber = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*$")]


class LegalSourceRecord(ContractModel):
    contract_version: Literal["1.1"] = "1.1"
    source_id: Identifier
    title: str = Field(min_length=1)
    authority: str = Field(min_length=1)
    document_type: str = Field(min_length=1)
    promulgated_on: date
    valid_from: date
    valid_to: date | None = None
    effective_status: EffectiveStatus
    jurisdiction: str = Field(min_length=1)
    official_source_url: AnyHttpUrl
    content_hash: ContentHash

    @model_validator(mode="after")
    def validate_effective_dates(self) -> LegalSourceRecord:
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not precede valid_from")
        return self


class ProvisionRecord(ContractModel):
    contract_version: Literal["1.1"] = "1.1"
    provision_id: Identifier
    source_id: Identifier
    article_no: ArticleNumber
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    book: str | None = Field(default=None, min_length=1)
    sub_book: str | None = Field(default=None, min_length=1)
    chapter: str | None = Field(default=None, min_length=1)
    section: str | None = Field(default=None, min_length=1)
    valid_from: date
    valid_to: date | None = None
    effective_status: EffectiveStatus
    jurisdiction: str = Field(min_length=1)
    maturity: MaturityLevel = MaturityLevel.L0
    source_span_ids: list[Identifier] = Field(min_length=1)
    content_hash: ContentHash

    @model_validator(mode="after")
    def validate_effective_dates(self) -> ProvisionRecord:
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not precede valid_from")
        return self
