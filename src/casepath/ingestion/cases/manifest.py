"""P3 dataset manifest. Integrity metadata stays out of the public contracts."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from casepath.contracts import ContractModel
from casepath.contracts.base import Identifier
from casepath.ingestion.laws.manifest import ManifestFile


class CaseReviewRecord(ContractModel):
    case_id: Identifier
    reviewed_on: date
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    note: str = Field(min_length=1)


class CaseDatasetManifest(ContractModel):
    manifest_version: Literal["1.1"] = "1.1"
    dataset_id: Identifier
    generated_on: date
    generator: str = Field(min_length=1)
    upstream_repository_url: str
    upstream_revision: str = Field(min_length=1)
    inputs: list[ManifestFile] = Field(min_length=1)
    outputs: list[ManifestFile] = Field(min_length=1)
    l1_case_count: int = Field(ge=0)
    deep_case_count: int = Field(ge=0)
    contract_versions: dict[str, str]
    reviewed_l2_case_ids: list[Identifier] = Field(default_factory=list)
    review_date: date | None = None
    reviews: list[CaseReviewRecord] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
