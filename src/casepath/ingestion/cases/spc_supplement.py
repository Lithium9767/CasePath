"""Load six source-checked editorial summaries, without inventing deep evidence.

The facts and reasoning are summaries of the SPC collection, not verbatim
judgments. Source provenance lives beside the release reports; this loader
deliberately creates no ClaimRecord, CourtFinding or SourceSpan.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from casepath.contracts import CaseIndexRecord, MaturityLevel
from casepath.ingestion.laws.jsonl import normalise_lf_bytes

INPUT_PATH = "data/inputs/spc_civil_code_first_batch.jsonl"
INPUT_SHA256 = "57205d17755ddbc8db470df229c62bb49c83f7584bf7778a766ba7ab66a868a4"
INPUT_REVISION = "casepath.spc-first-batch.summary.v1"
COUNT = 6
SOURCE_URL = "https://www.court.gov.cn/zixun/xiangqing/347181.html"
SOURCE_PUBLISHED_ON = "2022-02-25"
CHECKED_ON = "2026-09-13"
SUMMARY_PREFIX = "【编辑摘要，非判决原文】"

_PREFIX = "case.spc.civil-code-1."
_LAW_PREFIX = "law.prc.civil_code.2021.article_"
_SPECS = {
    _PREFIX + "huang-property-health": (4, "健康权纠纷", (286,)),
    _PREFIX + "logistics-v-wu": (6, "运输合同纠纷", (524,)),
    _PREFIX + "lou-portrait": (7, "肖像权纠纷", (1018, 1019, 1183)),
    _PREFIX + "su-statutory-inheritance": (8, "法定继承纠纷", (1128, 1131)),
    _PREFIX + "song-v-zhou-body-right": (10, "身体权纠纷", (1176, 1186)),
    _PREFIX + "yu-high-rise-object": (13, "高空抛物损害责任纠纷", (1254,)),
}
CASE_IDS = frozenset(_SPECS)
_FIELDS = {
    "case_id",
    "title",
    "case_no",
    "case_category",
    "case_cause",
    "court",
    "judgment_date",
    "case_facts_text",
    "judgment_reasoning_text",
    "judgment_summary",
    "keywords",
    "related_provision_ids",
    "source_file",
    "source_section",
    "text_kind",
}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("SPC input contains a duplicate JSON key")
        result[key] = value
    return result


def _text(row: dict[str, Any], field: str) -> str:
    value = row[field]
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"SPC {field} must be nonempty, trimmed text")
    return value


def _validate_records(rows: list[Any]) -> list[CaseIndexRecord]:
    if len(rows) != COUNT:
        raise ValueError(f"expected {COUNT} SPC summaries")
    records: list[CaseIndexRecord] = []
    seen_ids: set[str] = set()
    seen_facts: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != _FIELDS:
            raise ValueError("SPC summary fields differ from the pinned input schema")
        case_id = _text(row, "case_id")
        if case_id not in CASE_IDS or case_id in seen_ids:
            raise ValueError("SPC case IDs must be the six unique pinned identifiers")
        seen_ids.add(case_id)
        section, cause, articles = _SPECS[case_id]
        if row["source_file"] != SOURCE_URL or row["text_kind"] != "editorial_summary":
            raise ValueError("SPC source URL or editorial-summary designation mismatch")
        if type(row["source_section"]) is not int or row["source_section"] != section:
            raise ValueError("SPC source section mismatch")
        if row["case_category"] != "民事" or row["case_cause"] != cause:
            raise ValueError("SPC civil-case category or cause mismatch")
        # The collection does not supply a case number or identify the hearing
        # court, nor a confirmed final-instance date. Known first/unspecified-
        # instance dates are retained only in the editorial summary/provenance.
        if any(row[field] is not None for field in ("case_no", "court", "judgment_date")):
            raise ValueError("SPC undisclosed case number, court and final date must stay null")
        expected_provisions = [_LAW_PREFIX + f"{article:04d}" for article in articles]
        if row["related_provision_ids"] != expected_provisions:
            raise ValueError("SPC provisions must match the case's official article guidance")
        facts = _text(row, "case_facts_text")
        normalized_facts = re.sub(r"\s+", "", facts)
        if normalized_facts in seen_facts:
            raise ValueError("SPC facts must be unique after whitespace normalization")
        seen_facts.add(normalized_facts)
        keywords = row["keywords"]
        if (
            not isinstance(keywords, list)
            or not keywords
            or any(
                not isinstance(item, str) or not item.strip() or item != item.strip()
                for item in keywords
            )
            or len(keywords) != len(set(keywords))
        ):
            raise ValueError("SPC keywords must be unique nonempty strings")
        records.append(
            CaseIndexRecord(
                case_id=case_id,
                title=_text(row, "title"),
                cause=cause,
                keywords=keywords,
                facts_text=facts,
                reasoning_text=_text(row, "judgment_reasoning_text"),
                summary=SUMMARY_PREFIX + _text(row, "judgment_summary"),
                related_provision_ids=expected_provisions,
                maturity=MaturityLevel.L1,
            )
        )
    if seen_ids != CASE_IDS:
        raise ValueError("SPC case ID set mismatch")
    return sorted(records, key=lambda item: item.case_id)


def load_spc_cases(path: Path) -> list[CaseIndexRecord]:
    """Validate the LF-normalized pinned input and return six L1-only records."""

    raw = normalise_lf_bytes(path.read_bytes())
    if hashlib.sha256(raw).hexdigest() != INPUT_SHA256:
        raise ValueError("SPC editorial-summary input SHA256 mismatch")
    lines = raw.decode("utf-8").splitlines()
    if any(not line.strip() for line in lines):
        raise ValueError("SPC input must not contain blank records")
    return _validate_records([json.loads(line, object_pairs_hook=_unique_object) for line in lines])
