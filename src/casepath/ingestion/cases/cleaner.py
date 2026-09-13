from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
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
from .refinements import apply_case_overlay, uses_refund_condition_template
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
        facts, reasoning, _ = self._case_texts(case)
        return CaseIndexRecord(
            case_id=_case_id(case),
            title=str(case.get("title") or "未命名案例"),
            case_no=case.get("case_no") or None,
            court=case.get("court_name") or case.get("court") or None,
            judgment_date=_date(case.get("judgment_date")),
            cause=case.get("case_cause") or None,
            keywords=[str(item) for item in (case.get("keywords") or [])],
            facts_text=facts,
            reasoning_text=reasoning,
            summary=str(case.get("judgment_summary") or ""),
            related_provision_ids=related_provision_ids(case),
            maturity=MaturityLevel.L1,
        )

    def _case_texts(
        self, case: dict[str, Any], markdown: str | None = None
    ) -> tuple[str, str, str]:
        sections = split_sections(self.document_text(case, markdown))
        facts = str(sections.get("基本案情", case.get("case_facts") or ""))
        reasoning = str(sections.get("裁判理由", case.get("judgment_reasoning") or ""))
        result = sections.get("裁判结果", "")
        # CaseIndexRecord has no separate result text. Retain an explicit
        # result section verbatim in the indexed facts so offsets remain
        # resolvable by every downstream consumer of the canonical record.
        if result and result not in facts:
            facts = f"{facts}\n{result}".strip()
        return facts, reasoning, result

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

    def deep_record(self, case: dict[str, Any], markdown: str | None = None) -> CaseRecord:
        """Build a CaseRecord from a legal-rag civil case.

        Refund rule IDs are attached only after claim classification. Holdings
        and reasoning steps are omitted when they cannot be anchored in the
        canonical case text. Unreviewed machine output stays L1.
        """

        facts, reasoning, result_section = self._case_texts(case, markdown)
        # 【a 案例抽取】先分文书章节，再用 extract.py 的领域规则/短语匹配抽取结构。
        # 输入已有上游字段；这里不是通用 NER，不调用 embedding 判定实体关系。
        partitions = split_fact_partitions(facts)
        if result_section:
            result_holding = split_fact_partitions(result_section).holding or result_section
            partitions = replace(partitions, holding=result_holding)
            # A heading without "判决：" still delimits findings from the result.
            if result_section in partitions.court:
                partitions = replace(
                    partitions, court=partitions.court.split(result_section, 1)[0].strip()
                )
        holding = partitions.holding
        case_id = _case_id(case)
        book = SpanBook(case_id)
        claim = extract_claim(case_id, case, partitions, str(case.get("title") or ""))
        findings = extract_findings(case_id, partitions, facts, book)
        condition_findings = (
            # 仅指定对比集套用退费条件桥接；condition_id 及证据引用供之后建 ABOUT 边。
            # 请求、法院查明、说理、结果分别抽取，不能把诉称当作法院认定。
            extract_condition_findings(
                findings,
                facts,
                reasoning,
                holding,
                book,
            )
            if uses_refund_condition_template(case_id)
            else []
        )
        reasoning_step = extract_reasoning_step(
            case_id,
            reasoning,
            findings,
            book,
        )
        decision = extract_decision(case_id, claim, partitions, facts, reasoning, book)
        maturity = (
            MaturityLevel.L2 if case_id in REVIEWED_L2_CASE_IDS else MaturityLevel.L1
        )
        record = CaseRecord(
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
            decisions=[decision] if decision is not None else [],
            source_spans=book.spans,
        )
        return apply_case_overlay(record, facts, reasoning)

    def deep_fitness(self, case: dict[str, Any], markdown: str | None = None) -> CaseRecord:
        return self.deep_record(case, markdown)

    def build_records(
        self,
    ) -> tuple[list[CaseIndexRecord], list[CaseRecord]]:
        lightweight: list[CaseIndexRecord] = []
        deep: list[CaseRecord] = []
        for case in self.civil_cases():
            lightweight.append(self.lightweight(case))
            deep.append(self.deep_record(case))
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
