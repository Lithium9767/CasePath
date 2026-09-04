"""Civil-law source ingestion and canonicalisation."""

from .civil_code import (
    CIVIL_CODE_SOURCE_ID,
    EXPECTED_ARTICLE_COUNT,
    EXPECTED_SOURCE_SHA256,
    ConversionResult,
    convert_civil_code,
    load_civil_code,
)

__all__ = [
    "CIVIL_CODE_SOURCE_ID",
    "EXPECTED_ARTICLE_COUNT",
    "EXPECTED_SOURCE_SHA256",
    "ConversionResult",
    "convert_civil_code",
    "load_civil_code",
]
