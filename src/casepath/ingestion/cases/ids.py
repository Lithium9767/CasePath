"""Stable identifiers and the human-review allowlist for P3 cases."""

from __future__ import annotations

# Machine extraction never upgrades maturity or human_verified by itself.
# A case may enter this set only after claims, court findings, reasoning,
# decisions, and every ConditionFinding have been reviewed against the bound
# input and output hashes. UNKNOWN is allowed when the review record states
# that the source text cannot establish the condition.
REVIEWED_L2_CASE_IDS: frozenset[str] = frozenset()

CLAIM_TYPE_SERVICE_TERMINATION_REFUND = "服务合同解除与返还"
CLAIM_TYPE_GENERIC = "民事请求"

GYM_CASE_ID = "72XyEXKWpSmjK9JWf7MckMNbffcrh9paQ89GI5cnVy0%3D"
PROPERTY_CASE_ID = "LZ1lC0MXWZNp2jeA7I2uRzXsnvmbWIBuchDKfNYUIjE%3D"

EXPECTED_UPSTREAM_REPOSITORY_URL = "https://github.com/litunan/legal-rag"
EXPECTED_UPSTREAM_REVISION = "ce7872c7ae343e5ff860d627195ec4e72c7ef7ce"
EXPECTED_PROCESSED_CASES_PATH = "../legal-rag/data/processed/processed_cases.json"
# SHA-256 of the pinned Git blob after canonicalising newlines to LF.
EXPECTED_PROCESSED_CASES_SHA256 = (
    "7f3fde71d56b5410d1e49464710029bafc2b1e03ef0e2aef970136dfa80d6f9d"
)
EXPECTED_UPSTREAM_RECORD_COUNT = 51
EXPECTED_CIVIL_CASE_COUNT = 45
GENERATOR = "casepath.ingestion.cases.build:v1"
DATASET_ID = "dataset.civil_cases.v1"

# Filled after the first pinned release so later edits fail validation.
EXPECTED_CANONICAL_OUTPUT_HASHES: dict[str, str] = {
    "cases_l1.jsonl": "9845f4a9d54d619a2f5e77712f8f09a49e42637c329cfd03f8927fcc49df46dd",
    "cases_deep.jsonl": "96966e6b59dc82c5508909f77c4a741f9a47c9aa069496a2c1383df818643f3b",
}
