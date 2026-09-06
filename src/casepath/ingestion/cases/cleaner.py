from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from casepath.contracts import CaseIndexRecord, CaseRecord, MaturityLevel
from casepath.ingestion.laws.jsonl import write_jsonl

from .citations import related_provision_ids
from .classify import is_service_termination_refund
from .extract import (
    SpanBook,
    extract_claim,
    extract_condition_findings,
    extract_decision,
    extract_findings,
    extract_reasoning_step,
)
from .ids import REVIEWED_L2_CASE_IDS
from .sections import DATE_RE, split_fact_partitions, split_sections

__all__ = ["CaseCleaner", "clean_cases", "split_sections"]


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    match = DATE_RE.search(str(value or ""))
    if match is None:
        return None
    return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _case_id(case: dict[str, Any]) -> str:
    raw = str(case.get("case_id") or "").strip()
    if raw:
        return raw
    title = str(case.get("title") or "unnamed")
    digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:12]
    return f"case.{digest}"


@dataclass
class CaseCleaner:
    """Normalize upstream ``processed_cases.json`` into CasePath contracts."""

    input_path: Path
    markdown_dir: Path | None = None

    def load(self) -> list[dict[str, Any]]:
        payload = json.loads(self.input_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise TypeError("processed cases must be a JSON array")
        return payload

    def civil_cases(self) -> list[dict[str, Any]]:
        cases = [case for case in self.load() if case.get("case_category") == "民事"]
        return sorted(cases, key=lambda case: str(case.get("case_id") or ""))

    def document_text(self, case: dict[str, Any], markdown: str | None = None) -> str:
        if markdown:
            return markdown
        if self.markdown_dir is not None:
            path = self.markdown_dir / f"{case.get('title') or ''}.md"
            if path.is_file():
                return path.read_text(encoding="utf-8")
        full_document = str(case.get("full_document") or "").strip()
        if full_document:
            return full_document
        return (
            f"# {case.get('title') or ''}\n\n"
            f"## 基本案情\n{case.get('case_facts') or ''}\n\n"
            f"## 裁判理由\n{case.get('judgment_reasoning') or ''}\n"
        )

    def lightweight(self, case: dict[str, Any]) -> CaseIndexRecord:
        return CaseIndexRecord(
            case_id=_case_id(case),
            title=str(case.get("title") or "未命名案例"),
            case_no=case.get("case_no") or None,
            court=case.get("court_name") or case.get("court") or None,
            judgment_date=_date(case.get("judgment_date")),
            cause=case.get("case_cause") or None,
            keywords=[str(item) for item in (case.get("keywords") or [])],
            facts_text=str(case.get("case_facts") or ""),
            reasoning_text=str(case.get("judgment_reasoning") or ""),
            summary=str(case.get("judgment_summary") or ""),
            related_provision_ids=related_provision_ids(case),
            maturity=MaturityLevel.L1,
        )

    def plaintiff_text(self, case: dict[str, Any], markdown: str | None = None) -> str:
        document = self.document_text(case, markdown)
        sections = split_sections(document)
        facts = sections.get("基本案情", case.get("case_facts") or "")
        return split_fact_partitions(facts).plaintiff

    def is_service_termination_refund_case(
        self,
        case: dict[str, Any],
        markdown: str | None = None,
    ) -> bool:
        return is_service_termination_refund(case, self.plaintiff_text(case, markdown))

    def deep_fitness(self, case: dict[str, Any], markdown: str | None = None) -> CaseRecord:
        """Build a service-contract termination/refund deep record.

        Callers must already have classified the case. Unreviewed machine
        output is always L1 and ``human_verified=False``.
        """

        document = self.document_text(case, markdown)
        sections = split_sections(document)
        facts = sections.get("基本案情", case.get("case_facts") or "")
        reasoning = sections.get("裁判理由", case.get("judgment_reasoning") or "")
        result_section = sections.get("裁判结果", "")
        partitions = split_fact_partitions(facts)
        holding = result_section or partitions.holding
        if result_section and not partitions.holding:
            partitions = split_fact_partitions(f"{facts}\n{result_section}")
            holding = partitions.holding or result_section
        case_id = _case_id(case)
        book = SpanBook(case_id)
        claim = extract_claim(case_id, case, partitions, str(case.get("title") or ""))
        findings = extract_findings(case_id, partitions, facts, book)
        condition_findings = extract_condition_findings(
            findings,
            facts,
            reasoning,
            holding,
            book,
        )
        reasoning_step = extract_reasoning_step(
            case_id,
            reasoning,
            findings,
            claim.invoked_rule_ids,
            book,
        )
        decision = extract_decision(case_id, claim, partitions, facts, reasoning, book)
        maturity = (
            MaturityLevel.L2 if case_id in REVIEWED_L2_CASE_IDS else MaturityLevel.L1
        )
        return CaseRecord(
            case_id=case_id,
            title=str(case.get("title") or "未命名案例"),
            case_no=case.get("case_no") or None,
            court=case.get("court_name") or case.get("court") or None,
            judgment_date=_date(case.get("judgment_date")),
            cause=case.get("case_cause") or None,
            maturity=maturity,
            claims=[claim],
            findings=findings,
            condition_findings=condition_findings,
            reasoning_steps=[reasoning_step] if reasoning_step is not None else [],
            decisions=[decision],
            source_spans=book.spans,
        )

    def build_records(
        self,
    ) -> tuple[list[CaseIndexRecord], list[CaseRecord]]:
        lightweight: list[CaseIndexRecord] = []
        deep: list[CaseRecord] = []
        for case in self.civil_cases():
            lightweight.append(self.lightweight(case))
            if self.is_service_termination_refund_case(case):
                deep.append(self.deep_fitness(case))
        return lightweight, deep

    def write(self, output_path: Path, deep_output_path: Path | None = None) -> int:
        lightweight, deep = self.build_records()
        write_jsonl(output_path, lightweight)
        if deep_output_path is not None:
            write_jsonl(deep_output_path, deep)
        return len(lightweight)


def clean_cases(
    input_path: str | Path,
    output_path: str | Path,
    deep_output_path: str | Path | None = None,
    markdown_dir: str | Path | None = None,
) -> int:
    return CaseCleaner(
        Path(input_path),
        Path(markdown_dir) if markdown_dir else None,
    ).write(
        Path(output_path),
        Path(deep_output_path) if deep_output_path else None,
    )
