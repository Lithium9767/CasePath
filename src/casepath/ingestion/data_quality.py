"""只读正式数据的可重复质量报告；结构检查通过不等于法律语义完整或人工审核。"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from casepath.contracts import (
    CaseIndexRecord,
    CaseRecord,
    LegalSourceRecord,
    ProvisionRecord,
    RuleRecord,
    SourceSpan,
)
from casepath.ingestion.cases.validation import validate_records as validate_case_records
from casepath.ingestion.laws.civil_code import CIVIL_CODE_SOURCE_ID, provision_id
from casepath.ingestion.laws.jsonl import sha256_file, sha256_text, write_json

GENERATED_ON = "2026-09-11"
FILES = {
    "canonical/rules/legal_sources.jsonl": LegalSourceRecord,
    "canonical/rules/provisions.jsonl": ProvisionRecord,
    "canonical/rules/rules.jsonl": RuleRecord,
    "canonical/rules/source_spans.jsonl": SourceSpan,
    "canonical/cases/cases_l1.jsonl": CaseIndexRecord,
    "canonical/cases/cases_deep.jsonl": CaseRecord,
}
MANIFESTS = ("manifests/civil_code.manifest.json", "manifests/cases.manifest.json")
BOOKS = (
    ("第一编 总则", 1, 204),
    ("第二编 物权", 205, 462),
    ("第三编 合同", 463, 988),
    ("第四编 人格权", 989, 1039),
    ("第五编 婚姻家庭", 1040, 1118),
    ("第六编 继承", 1119, 1163),
    ("第七编 侵权责任", 1164, 1258),
    ("附则", 1259, 1260),
)


def _new_report() -> dict:
    return {
        "report_version": "1.0",
        "generated_on": GENERATED_ON,
        "status": "passed",
        "scope": "canonical structural and reference audit; no upstream inputs or live services",
        "limitations": [
            "Article coverage measures presence and provision references, not semantic completeness.",
            "Warnings are review candidates, not proven legal errors or automatic merge decisions.",
            "This report does not grant maturity or replace the release manifest validators.",
        ],
        "sha256_convention": "UTF-8 file bytes with CRLF and CR normalized to LF",
        "files": {},
        "counts": {},
        "maturities": {},
        "checks": [],
        "errors": [],
        "warnings": [],
    }


def _issue(report: dict, code: str, *, warning: bool = False, **location) -> None:
    report["warnings" if warning else "errors"].append({"code": code, **location})


def _duplicates(values) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


def _article(span: SourceSpan) -> int | None:
    match = re.fullmatch(r"第(\d+)条", span.section or "")
    return int(match[1]) if match else None


def _definition(entity) -> str:
    return json.dumps(entity.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


def _check(report: dict, name: str, operation) -> None:
    before = len(report["errors"])
    operation()
    report["checks"].append(
        {"name": name, "status": "passed" if len(report["errors"]) == before else "failed"}
    )


def audit_records(
    sources: list[LegalSourceRecord],
    provisions: list[ProvisionRecord],
    rules: list[RuleRecord],
    spans: list[SourceSpan],
    lightweight: list[CaseIndexRecord],
    deep: list[CaseRecord],
    *,
    expected_article_count: int = 1260,
) -> dict:
    """Audit already parsed records; the CLI always requires all 1260 articles."""
    report = _new_report()
    conditions = [condition for rule in rules for condition in rule.conditions]
    report["counts"] = {
        "legal_sources": len(sources),
        "provisions": len(provisions),
        "rules": len(rules),
        "law_source_spans": len(spans),
        "cases_l1": len(lightweight),
        "cases_deep": len(deep),
        "condition_occurrences": len(conditions),
        "unique_condition_ids": len({item.condition_id for item in conditions}),
        "unique_condition_predicates": len({item.predicate for item in conditions}),
    }
    report["maturities"] = {
        name: dict(sorted(Counter(item.maturity.value for item in records).items()))
        for name, records in (
            ("provisions", provisions),
            ("rules", rules),
            ("cases_l1", lightweight),
            ("cases_deep", deep),
        )
    }
    source_ids = {item.source_id for item in sources}
    provision_by_id = {item.provision_id: item for item in provisions}
    provision_by_number = {
        int(item.article_no): item
        for item in provisions
        if item.article_no.isascii() and item.article_no.isdecimal()
    }
    span_by_id = {item.span_id: item for item in spans}
    rule_ids = {item.rule_id for item in rules}

    def identity():
        for name, records, field in (
            ("legal_sources", sources, "source_id"),
            ("provisions", provisions, "provision_id"),
            ("rules", rules, "rule_id"),
            ("law_source_spans", spans, "span_id"),
            ("cases_l1", lightweight, "case_id"),
            ("cases_deep", deep, "case_id"),
        ):
            for duplicate in _duplicates(getattr(item, field) for item in records):
                location = (
                    {"id_sha256": sha256_text(duplicate)}
                    if name.startswith("cases")
                    else {"id": duplicate}
                )
                _issue(report, "duplicate_id", collection=name, **location)
        numbers = [
            int(p.article_no)
            for p in provisions
            if p.article_no.isascii() and p.article_no.isdecimal()
        ]
        expected = set(range(1, expected_article_count + 1))
        if (
            len(numbers) != len(provisions)
            or len(numbers) != len(set(numbers))
            or set(numbers) != expected
        ):
            _issue(
                report,
                "article_sequence",
                missing=sorted(expected - set(numbers)),
                unexpected=sorted(set(numbers) - expected),
                duplicate_numbers=_duplicates(numbers),
                invalid_number_count=len(provisions) - len(numbers),
            )
        for p in provisions:
            if p.source_id not in source_ids:
                _issue(report, "unknown_legal_source", provision_id=p.provision_id)
            if (
                p.article_no.isascii()
                and p.article_no.isdecimal()
                and (
                    p.provision_id != provision_id(int(p.article_no))
                    or p.source_id != CIVIL_CODE_SOURCE_ID
                )
            ):
                _issue(report, "noncanonical_provision_id", provision_id=p.provision_id)
            if not p.text.strip():
                _issue(report, "empty_provision_text", provision_id=p.provision_id)
        for index, case in enumerate(lightweight, start=1):
            if not case.facts_text.strip() and not case.reasoning_text.strip():
                _issue(report, "empty_case_text", case_row=index)

    _check(report, "identities_article_sequence_and_text", identity)

    def legal_spans():
        embedded = [s for p in provisions for s in p.source_spans]
        embedded += [s for r in rules for s in r.source_spans]
        for span in embedded:
            if span_by_id.get(span.span_id) != span:
                _issue(report, "embedded_span_missing_or_conflicting", span_id=span.span_id)
        for span in spans:
            parent = provision_by_number.get(_article(span))
            if parent is None or parent.source_id != span.source_id:
                _issue(report, "law_span_parent_missing", span_id=span.span_id)
            elif (
                not span.quote.strip()
                or span.end_offset > len(parent.text)
                or parent.text[span.start_offset : span.end_offset] != span.quote
            ):
                _issue(report, "law_span_quote_offset_mismatch", span_id=span.span_id)
        for p in provisions:
            if not any(
                s.start_offset == 0 and s.end_offset == len(p.text) and s.quote == p.text
                for s in p.source_spans
            ):
                _issue(report, "provision_full_span_missing", provision_id=p.provision_id)

    _check(report, "law_span_registry_and_exact_offsets", legal_spans)
    omissions = []
    shared = {}
    predicate_owners = defaultdict(list)
    semantic_owners = defaultdict(list)
    empty_diagnostic = []
    diagnostic_candidates = []

    def rule_references():
        for rule in rules:
            own_spans = {s.span_id: s for s in rule.source_spans}
            declared = {p.provision_id for p in rule.provisions}
            for ref in rule.provisions:
                p = provision_by_id.get(ref.provision_id)
                if p is None:
                    _issue(
                        report,
                        "rule_unknown_provision",
                        rule_id=rule.rule_id,
                        provision_id=ref.provision_id,
                    )
                elif (ref.source_id, ref.article_no, ref.title, ref.valid_from, ref.valid_to) != (
                    p.source_id,
                    p.article_no,
                    p.title,
                    p.effective_from,
                    p.effective_to,
                ):
                    _issue(
                        report,
                        "rule_provision_metadata_mismatch",
                        rule_id=rule.rule_id,
                        provision_id=ref.provision_id,
                    )
            for span in rule.source_spans:
                parent = provision_by_number.get(_article(span))
                if parent is not None and parent.provision_id not in declared:
                    omission = {
                        "rule_id": rule.rule_id,
                        "span_id": span.span_id,
                        "provision_id": parent.provision_id,
                    }
                    omissions.append(omission)
                    _issue(report, "span_parent_provision_not_declared", **omission)
            roles = defaultdict(set)
            for group in rule.condition_groups:
                for condition_id in group.member_condition_ids:
                    roles[condition_id].add(group.role.value)
            for attr, field in (
                ("conditions", "condition_id"),
                ("exceptions", "exception_id"),
                ("consequences", "consequence_id"),
            ):
                for entity in getattr(rule, attr):
                    entity_id = getattr(entity, field)
                    key = (attr, entity_id)
                    definition = _definition(entity)
                    if key in shared and shared[key] != definition:
                        _issue(
                            report,
                            "shared_entity_definition_conflict",
                            entity_id=entity_id,
                            entity_type=attr,
                            rule_id=rule.rule_id,
                        )
                    shared.setdefault(key, definition)
                    for span_id in entity.source_span_ids:
                        if span_id not in own_spans:
                            _issue(
                                report,
                                "entity_span_not_embedded",
                                rule_id=rule.rule_id,
                                entity_id=entity_id,
                                span_id=span_id,
                            )
                    diagnostic = attr == "conditions" and roles[entity_id] == {"DIAGNOSTIC"}
                    if not entity.source_span_ids:
                        if diagnostic:
                            empty_diagnostic.append(
                                {"rule_id": rule.rule_id, "condition_id": entity_id}
                            )
                        else:
                            _issue(
                                report,
                                "required_entity_evidence_missing",
                                rule_id=rule.rule_id,
                                entity_id=entity_id,
                            )
                    if attr == "conditions":
                        owner = {"rule_id": rule.rule_id, "condition_id": entity_id}
                        predicate_owners[entity.predicate].append(owner)
                        semantic = entity.model_dump(exclude={"condition_id", "source_span_ids"})
                        semantic_owners[json.dumps(semantic, sort_keys=True)].append(owner)
                        cue_spans = sorted(
                            sid
                            for sid in entity.source_span_ids
                            if sid in own_spans
                            and re.search(r"除外|方可|须经|必须|未经", own_spans[sid].quote)
                        )
                        if diagnostic and cue_spans:
                            diagnostic_candidates.append({**owner, "span_ids": cue_spans})

    _check(report, "rule_shared_definitions_evidence_and_parent_provisions", rule_references)
    report["parent_reference_omissions"] = omissions
    report["counts"]["parent_reference_omissions"] = len(omissions)
    report["diagnostic_conditions_without_source"] = empty_diagnostic
    report["diagnostic_legal_premise_review_candidates"] = diagnostic_candidates
    if diagnostic_candidates:
        _issue(
            report,
            "diagnostic_source_contains_prerequisite_or_exception_cue",
            warning=True,
            count=len(diagnostic_candidates),
            method="Source contains 除外/方可/须经/必须/未经; contextual legal review required.",
        )

    def duplicate_groups(owners):
        return [
            {
                "definition_sha256": sha256_text(definition),
                "condition_ids": sorted({o["condition_id"] for o in entries}),
                "rule_ids": sorted({o["rule_id"] for o in entries}),
            }
            for definition, entries in sorted(owners.items())
            if len({o["condition_id"] for o in entries}) > 1
        ]

    report["duplicate_condition_predicates"] = duplicate_groups(predicate_owners)
    report["duplicate_condition_definitions"] = duplicate_groups(semantic_owners)
    if report["duplicate_condition_predicates"]:
        _issue(
            report,
            "same_predicate_different_condition_ids",
            warning=True,
            count=len(report["duplicate_condition_predicates"]),
        )

    def case_references():
        index_by_id = {case.case_id: case for case in lightweight}
        condition_ids = {condition.condition_id for condition in conditions}
        for index, case in enumerate(lightweight, start=1):
            for pid in case.related_provision_ids:
                if pid not in provision_by_id:
                    _issue(report, "case_unknown_provision", case_row=index, provision_id=pid)
        try:
            validate_case_records(lightweight, deep, provisions, rules)
        except ValueError:
            # Existing validator messages may include case identifiers; never echo raw content.
            _issue(report, "existing_case_record_validator_failed")
        for index, case in enumerate(deep, start=1):
            parent = index_by_id.get(case.case_id)
            if parent is None:
                _issue(report, "deep_case_missing_from_index", case_row=index)
            for finding in case.condition_findings:
                if finding.condition_id not in condition_ids:
                    _issue(
                        report,
                        "case_bridge_unknown_condition",
                        case_row=index,
                        condition_id=finding.condition_id,
                    )
            for claim in case.claims:
                for rule_id in claim.invoked_rule_ids:
                    if rule_id not in rule_ids:
                        _issue(report, "case_claim_unknown_rule", case_row=index, rule_id=rule_id)
            for span_index, span in enumerate(case.source_spans, start=1):
                if parent is not None and not any(
                    span.quote.strip()
                    and document[span.start_offset : span.end_offset] == span.quote
                    for document in (parent.facts_text, parent.reasoning_text)
                ):
                    _issue(
                        report,
                        "case_span_quote_offset_mismatch",
                        case_row=index,
                        span_index=span_index,
                    )
            for step in case.reasoning_steps:
                for rule_id in step.applied_rule_ids:
                    if rule_id not in rule_ids:
                        _issue(
                            report, "case_reasoning_unknown_rule", case_row=index, rule_id=rule_id
                        )

    _check(report, "case_contracts_bridges_and_exact_offsets", case_references)
    referenced = {p.provision_id for rule in rules for p in rule.provisions}
    report["article_coverage"] = [
        {
            "book": book,
            "first_article": first,
            "last_article": last,
            "expected_articles": last - first + 1,
            "present_articles": sum(first <= n <= last for n in provision_by_number),
            "rule_referenced_articles": sum(
                first <= n <= last and p.provision_id in referenced
                for n, p in provision_by_number.items()
            ),
        }
        for book, first, last in BOOKS
    ]
    report["status"] = "failed" if report["errors"] else "passed"
    return report


def build_report(data_root: Path) -> dict:
    """Read canonical files plus manifest hashes, without reconstructing upstream inputs."""
    data_root = data_root.resolve()
    input_errors = []
    records = []
    hashes = {}
    for relative in (*FILES, *MANIFESTS):
        path = data_root / relative
        try:
            hashes[relative] = sha256_file(path)
        except OSError:
            input_errors.append({"code": "unreadable_formal_file", "file": relative})
    for relative, model in FILES.items():
        parsed = []
        try:
            lines = (data_root / relative).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            input_errors.append({"code": "unreadable_canonical_file", "file": relative})
            lines = []
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                parsed.append(model.model_validate_json(line))
            except ValueError:
                input_errors.append(
                    {
                        "code": "record_contract_validation_failed",
                        "file": relative,
                        "line": line_number,
                    }
                )
        records.append(parsed)
    report = audit_records(*records)
    report["files"] = {name: {"sha256": digest} for name, digest in sorted(hashes.items())}
    report["errors"] = input_errors + report["errors"]
    report["checks"].insert(
        0,
        {
            "name": "formal_files_and_record_contracts",
            "status": "failed" if input_errors else "passed",
        },
    )
    changed = []
    for name, digest in hashes.items():
        try:
            if sha256_file(data_root / name) != digest:
                changed.append(name)
        except OSError:
            changed.append(name)
    for name in changed:
        _issue(report, "file_changed_during_audit", file=name)
    report["checks"].append(
        {"name": "stable_file_snapshot", "status": "failed" if changed else "passed"}
    )
    report["status"] = "failed" if report["errors"] else "passed"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.output is not None:
        destination = args.output.resolve()
        if any(
            destination.is_relative_to((args.data_root / name).resolve())
            for name in ("canonical", "manifests", "inputs")
        ):
            parser.error("--output cannot overwrite canonical data, manifests, or inputs")
    report = build_report(args.data_root)
    if args.output is not None:
        write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
