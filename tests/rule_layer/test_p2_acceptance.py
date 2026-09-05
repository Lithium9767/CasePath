from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from casepath.contracts import RuleCondition, RuleException, RuleRecord, SourceSpan

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_ROOT = REPOSITORY_ROOT / "data" / "canonical" / "rules"
SCHEMA_ROOT = REPOSITORY_ROOT / "contracts" / "schemas"


@pytest.mark.parametrize(
    ("filename", "schema_name", "expected_count", "definition"),
    [
        ("legal_sources.jsonl", "legal-source-record", 1, None),
        ("provisions.jsonl", "provision-record", 1260, None),
        ("rules.jsonl", "rule-record", 5, None),
        ("source_spans.jsonl", "provision-record", 1268, "SourceSpan"),
    ],
)
def test_published_records_pass_exported_json_schema(
    filename: str, schema_name: str, expected_count: int, definition: str | None
) -> None:
    schema = json.loads((SCHEMA_ROOT / f"{schema_name}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    if definition is not None:
        # Standalone spans reuse the exported embedded contract, not a new public schema.
        assert definition in schema["$defs"]
        schema = {"$defs": schema["$defs"], "$ref": f"#/$defs/{definition}"}
        Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    records = (CANONICAL_ROOT / filename).read_text(encoding="utf-8").splitlines()
    assert len(records) == expected_count
    for line_number, line in enumerate(records, start=1):
        # Validate the raw JSON before Pydantic could coerce field types.
        errors = list(validator.iter_errors(json.loads(line)))
        assert not errors, f"{filename}:{line_number}: " + "; ".join(
            f"{error.json_path}: {error.message}" for error in errors
        )


@pytest.fixture
def isolated_rules_file(tmp_path: Path) -> Path:
    destination = tmp_path / "rules.jsonl"
    shutil.copyfile(CANONICAL_ROOT / "rules.jsonl", destination)
    return destination


def test_rules_file_alone_supports_indexes_and_complete_references(
    isolated_rules_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(isolated_rules_file.parent)
    assert {path.name for path in Path.cwd().iterdir()} == {"rules.jsonl"}
    # From this point only the isolated file and public RuleRecord contract are needed.
    records = [
        RuleRecord.model_validate_json(line)
        for line in Path("rules.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    rule_index: dict[str, RuleRecord] = {}
    fact_index: dict[str, RuleCondition | RuleException] = {}
    rules_by_fact: dict[str, set[str]] = {}
    span_index: dict[str, SourceSpan] = {}
    search_documents: dict[str, str] = {}

    for rule in records:
        assert rule.rule_id not in rule_index
        rule_index[rule.rule_id] = rule
        embedded_spans = {span.span_id: span for span in rule.source_spans}
        assert len(embedded_spans) == len(rule.source_spans)
        source_ids = {provision.source_id for provision in rule.provisions}
        for span in rule.source_spans:
            assert span.source_id in source_ids
            assert span.end_offset - span.start_offset == len(span.quote)
            assert span_index.setdefault(span.span_id, span) == span

        sourced_items = [*rule.conditions, *rule.exceptions, *rule.consequences]
        for item in sourced_items:
            assert item.source_span_ids
            assert set(item.source_span_ids) <= embedded_spans.keys()

        fact_pairs = [
            *((condition.condition_id, condition) for condition in rule.conditions),
            *((exception.exception_id, exception) for exception in rule.exceptions),
        ]
        for fact_id, fact in fact_pairs:
            assert fact_index.setdefault(fact_id, fact) == fact
            rules_by_fact.setdefault(fact_id, set()).add(rule.rule_id)

        condition_ids = {condition.condition_id for condition in rule.conditions}
        grouped_ids = {
            condition_id
            for group in rule.condition_groups
            for condition_id in group.member_condition_ids
        }
        assert grouped_ids == condition_ids

        # This is a test-only searchable document, not a retrieval/scoring implementation.
        text_fields = [rule.title, *rule.claim_types]
        for provision in rule.provisions:
            assert provision.provision_id and provision.source_id
            text_fields.extend([provision.article_no, provision.title])
        for condition in rule.conditions:
            text_fields.extend([condition.label, condition.predicate, *condition.evidence_types])
        for group in rule.condition_groups:
            text_fields.append(group.label)
        for exception in rule.exceptions:
            text_fields.extend([exception.label, exception.predicate, exception.effect])
        for consequence in rule.consequences:
            text_fields.extend([consequence.consequence_type, consequence.description])
        text_fields.extend(span.quote for span in rule.source_spans)
        assert all(isinstance(value, str) and value.strip() for value in text_fields)
        search_documents[rule.rule_id] = "\n".join(text_fields)

    assert len(records) == len(rule_index) == 5
    assert sum(rule.maturity == "L3" for rule in records) == 4
    assert set(rule_index) == {
        "rule.contract.performance.good_faith.v1",
        "rule.contract.termination.delay_after_demand.v1",
        "rule.contract.termination.nonperformance.v1",
        "rule.contract.termination.restitution.v1",
        "rule.service_contract.termination_refund.v1",
    }
    assert set(fact_index) == {
        "cond.contract_exists",
        "cond.main_obligation_delayed",
        "cond.demand_delivered",
        "cond.reasonable_period_expired",
        "cond.performance_impossible",
        "cond.alternative_performance",
        "cond.contract_terminated",
        "cond.payment_made",
        "cond.unperformed_balance",
    }
    assert len(span_index) == 8
    assert isinstance(fact_index["cond.alternative_performance"], RuleException)
    assert rules_by_fact["cond.alternative_performance"] == {
        "rule.contract.termination.nonperformance.v1",
        "rule.service_contract.termination_refund.v1",
    }
    assert set(search_documents) == set(rule_index)
    matching_rules = {
        rule_id for rule_id, document in search_documents.items() if "催告" in document
    }
    assert "rule.contract.termination.delay_after_demand.v1" in matching_rules
