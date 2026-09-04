from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from casepath.contracts import ContractModel
from casepath.contracts.base import Identifier


class ManifestFile(ContractModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_count: int | None = Field(default=None, ge=0)
    repository_url: str | None = None
    revision: str | None = None


class TransformationRecord(ContractModel):
    transformation_id: Identifier
    description: str = Field(min_length=1)
    affected_records: int = Field(ge=0)
    guard_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AuthorityVerification(ContractModel):
    review_status: Literal["human_verified"] = "human_verified"
    verified_on: date
    effective_status: Literal["effective"] = "effective"
    checked_article_numbers: list[int] = Field(min_length=1)
    whitespace_normalized_sha256: dict[str, str]
    source_urls: list[str] = Field(min_length=1)
    note: str = Field(min_length=1)


class CivilCodeManifest(ContractModel):
    manifest_version: Literal["1.0"] = "1.0"
    dataset_id: Identifier
    source_id: Identifier
    generated_on: date
    generator: str = Field(min_length=1)
    upstream_repository_url: str
    upstream_revision: str = Field(min_length=1)
    upstream_license_status: Literal["not_declared"] = "not_declared"
    inputs: list[ManifestFile] = Field(min_length=1)
    outputs: list[ManifestFile] = Field(min_length=1)
    transformations: list[TransformationRecord] = Field(default_factory=list)
    authority_verification: AuthorityVerification
    rule_review_status: dict[str, Literal["human_verified", "needs_additional_authority"]]
    limitations: list[str] = Field(default_factory=list)
