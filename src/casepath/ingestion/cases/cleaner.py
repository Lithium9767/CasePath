from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from casepath.contracts import CaseRecord, ClaimRecord, CourtFinding, DecisionItem, SourceSpan


SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
DATE_RE = re.compile(r"(\d{4})[.年/-](\d{1,2})[.月/-](\d{1,2})")


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    m = DATE_RE.search(str(value or ""))
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _source_span(source_id: str, section: str, text: str, paragraph: int = 1) -> SourceSpan:
    return SourceSpan(
        span_id=f"span.{source_id}.{paragraph}", source_id=source_id, section=section,
        paragraph_id=f"{section}-{paragraph}", start_offset=0, end_offset=len(text),
        quote=text, content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def split_sections(markdown: str) -> dict[str, str]:
    matches = list(SECTION_RE.finditer(markdown))
    sections: dict[str, str] = {}
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        sections[match.group(1).strip()] = markdown[start:end].strip()
    return sections


@dataclass
class CaseCleaner:
    """Normalize upstream ``processed_cases.json`` into CasePath contracts."""

    input_path: Path
    markdown_dir: Path | None = None

    def load(self) -> list[dict[str, Any]]:
        payload = json.loads(self.input_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("processed cases must be a JSON array")
        return payload

    def civil_cases(self) -> list[dict[str, Any]]:
        return [c for c in self.load() if c.get("case_category") == "民事"]

    def lightweight(self, case: dict[str, Any]) -> dict[str, Any]:
        return {
            "case_id": str(case.get("case_id") or ""), "title": case.get("title") or "",
            "case_no": case.get("case_no"), "case_category": case.get("case_category"),
            "case_cause": case.get("case_cause"), "court": case.get("court_name"),
            "judgment_date": _date(case.get("judgment_date")),
            "keywords": case.get("keywords") or [], "case_facts_text": case.get("case_facts") or "",
            "judgment_reasoning_text": case.get("judgment_reasoning") or "",
            "judgment_summary": case.get("judgment_summary") or "",
            "related_provision_ids": [f"provision.civil_code.{p.get('article')}" for p in (case.get("legal_provisions") or []) if p.get("article")],
            "source_file": case.get("source_file"),
        }

    def deep_fitness(self, case: dict[str, Any], markdown: str | None = None) -> CaseRecord:
        md = markdown or case.get("full_document") or ""
        sections = split_sections(md)
        source_id = f"case.{case.get('case_id') or hashlib.sha1((case.get('title') or '').encode()).hexdigest()[:12]}"
        facts = sections.get("基本案情", case.get("case_facts") or "")
        reasoning = sections.get("裁判理由", case.get("judgment_reasoning") or "")
        result = sections.get("裁判结果", "")
        spans = [_source_span(source_id, "基本案情", facts), _source_span(source_id, "裁判理由", reasoning)]
        claims = [ClaimRecord(claim_id="claim.primary", claim_type=case.get("case_cause") or "民事请求", requested_remedy="当事人请求依法裁判")]
        findings = [CourtFinding(finding_id="finding.facts", predicate=facts[:500] or "法院认定事实待补充", source_span_ids=[spans[0].span_id])]
        decisions = []
        if result:
            spans.append(_source_span(source_id, "裁判结果", result))
            decisions.append(DecisionItem(decision_id="decision.primary", claim_id=claims[0].claim_id, status="UNKNOWN", description=result[:500], source_span_ids=[spans[-1].span_id]))
        else:
            decisions.append(DecisionItem(decision_id="decision.primary", claim_id=claims[0].claim_id, status="UNKNOWN", description="原始文书未提供裁判结果，待人工核验。", source_span_ids=[spans[1].span_id]))
        return CaseRecord(case_id=str(case.get("case_id") or source_id), title=case.get("title") or "未命名案例", case_no=case.get("case_no"), court=case.get("court_name"), judgment_date=_date(case.get("judgment_date")), cause=case.get("case_cause"), maturity="L2", claims=claims, findings=findings, decisions=decisions, source_spans=spans)

    def write(self, output_path: Path, deep_output_path: Path | None = None) -> int:
        cases = self.civil_cases()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            for c in cases:
                row = self.lightweight(c); row["judgment_date"] = row["judgment_date"].isoformat() if row["judgment_date"] else None
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if deep_output_path:
            deep_output_path.parent.mkdir(parents=True, exist_ok=True)
            with deep_output_path.open("w", encoding="utf-8") as f:
                for c in cases:
                    if "健身" in (c.get("title") or ""):
                        md = None
                        if self.markdown_dir:
                            p = self.markdown_dir / f"{c['title']}.md"
                            if p.exists(): md = p.read_text(encoding="utf-8")
                        f.write(self.deep_fitness(c, md).model_dump_json(ensure_ascii=False) + "\n")
        return len(cases)


def clean_cases(input_path: str | Path, output_path: str | Path, deep_output_path: str | Path | None = None, markdown_dir: str | Path | None = None) -> int:
    return CaseCleaner(Path(input_path), Path(markdown_dir) if markdown_dir else None).write(Path(output_path), Path(deep_output_path) if deep_output_path else None)
