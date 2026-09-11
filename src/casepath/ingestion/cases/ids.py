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
EXPECTED_LEGAL_RAG_CIVIL_COUNT = 45
EXPECTED_LEXREL_SERVICE_CASE_COUNT = 9
EXPECTED_LEXREL_RETRIEVAL_CASE_COUNT = 96
EXPECTED_LEXREL_CASE_COUNT = 105
EXPECTED_CIVIL_CASE_COUNT = 150
EXPECTED_DEEP_CASE_COUNT = 150
GENERATOR = "casepath.ingestion.cases.build:v6"
DATASET_ID = "dataset.civil_cases.v1"

EXPECTED_LEXREL_REPOSITORY_URL = "https://github.com/thunlp/LexRel"
EXPECTED_LEXREL_REVISION = "5d04570483da5726d5db99d72633c3ab919b4e4b"
EXPECTED_LEXREL_SERVICE_PATH = "data/inputs/lexrel_service_contract.json"
EXPECTED_LEXREL_SERVICE_SHA256 = (
    "2b62543e8c61eb44664a1066a8a32311c026601ee02c7e4963799e1087a3285e"
)
EXPECTED_LEXREL_SERVICE_RECORD_COUNT = 9
EXPECTED_LEXREL_RETRIEVAL_PATH = "data/inputs/lexrel_retrieval_cases.json"
EXPECTED_LEXREL_RETRIEVAL_SHA256 = (
    "3e7ef52e191217816c97b2fbcab075089e165b65b30dcbaef7d05c684aa95d24"
)
EXPECTED_LEXREL_RETRIEVAL_RECORD_COUNT = 96
LEXREL_CASE_ID_PREFIX = "lexrel."

# Filled after the first pinned release so later edits fail validation.
EXPECTED_CANONICAL_OUTPUT_HASHES: dict[str, str] = {
    "cases_l1.jsonl": "86013cdc1266f821cf74fb9664fe97d1d6c878f8d75d1af3d94e52e1d90323f3",
    "cases_deep.jsonl": "26a24b778ff4a589fc42b28b1335d6763cef527ae8dfa407339b31dbd5176017",
}
