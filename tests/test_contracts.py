import json
from hashlib import sha256
from pathlib import Path

import pytest

from casepath.contracts import (
    CaseRecord,
    ExplanationPlan,
    LegalSourceRecord,
    MaturityLevel,
    ProvisionRecord,
    QueryState,
    RetrievalBundle,
    RuleRecord,
)

EXAMPLES = Path("contracts/examples")
SCHEMAS = Path("contracts/schemas")
CANONICAL_RULES = Path("data/canonical/rules")

CONTRACT_CASES = [
    ("legal-source-record", LegalSourceRecord, "1.1"),
    ("provision-record", ProvisionRecord, "1.1"),
    ("rule-record", RuleRecord, "1.0"),
    ("case-record", CaseRecord, "1.0"),
    ("query-state", QueryState, "1.0"),
    ("retrieval-bundle", RetrievalBundle, "1.0"),
    ("explanation-plan", ExplanationPlan, "1.0"),
]


@pytest.mark.parametrize(
    ("filename", "model", "contract_version"),
    [(f"{name}.json", model, version) for name, model, version in CONTRACT_CASES],
)
def test_examples_match_frozen_contracts(filename, model, contract_version):
    payload = json.loads((EXAMPLES / filename).read_text(encoding="utf-8"))
    parsed = model.model_validate(payload)
    assert parsed.contract_version == contract_version


@pytest.mark.parametrize(("name", "model"), [(name, model) for name, model, _ in CONTRACT_CASES])
def test_exported_json_schemas_match_runtime_models(name, model):
    exported = json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))
    assert exported == model.model_json_schema()


def test_contracts_reject_unknown_fields():
    payload = json.loads((EXAMPLES / "query-state.json").read_text(encoding="utf-8"))
    payload["invented_field"] = "must fail"
    with pytest.raises(ValueError):
        QueryState.model_validate(payload)


def test_provision_article_number_is_an_unpadded_string_and_maturity_defaults_to_l0():
    payload = json.loads((EXAMPLES / "provision-record.json").read_text(encoding="utf-8"))
    payload.pop("maturity")

    parsed = ProvisionRecord.model_validate(payload)

    assert parsed.article_no == "509"
    assert isinstance(parsed.article_no, str)
    assert parsed.maturity == MaturityLevel.L0

    payload["article_no"] = "0509"
    with pytest.raises(ValueError):
        ProvisionRecord.model_validate(payload)


@pytest.mark.parametrize("content_hash", ["A" * 64, "0" * 63, "not-a-sha256"])
def test_source_contracts_reject_invalid_content_hash(content_hash):
    payload = json.loads((EXAMPLES / "legal-source-record.json").read_text(encoding="utf-8"))
    payload["content_hash"] = content_hash

    with pytest.raises(ValueError):
        LegalSourceRecord.model_validate(payload)


def test_source_contracts_reject_inverted_dates_and_missing_provision_spans() -> None:
    source_payload = json.loads((EXAMPLES / "legal-source-record.json").read_text(encoding="utf-8"))
    source_payload["valid_to"] = "2020-12-31"
    with pytest.raises(ValueError, match="valid_to must not precede valid_from"):
        LegalSourceRecord.model_validate(source_payload)

    provision_payload = json.loads((EXAMPLES / "provision-record.json").read_text(encoding="utf-8"))
    provision_payload["valid_to"] = "2020-12-31"
    with pytest.raises(ValueError, match="valid_to must not precede valid_from"):
        ProvisionRecord.model_validate(provision_payload)

    provision_payload["valid_to"] = None
    provision_payload["source_span_ids"] = []
    with pytest.raises(ValueError):
        ProvisionRecord.model_validate(provision_payload)


def test_rule_example_contains_real_replayable_source_spans():
    payload = json.loads((EXAMPLES / "rule-record.json").read_text(encoding="utf-8"))
    rule = RuleRecord.model_validate(payload)

    assert "占位" not in json.dumps(payload, ensure_ascii=False)
    for span in rule.source_spans:
        assert 0 <= span.start_offset < span.end_offset
        assert span.end_offset - span.start_offset == len(span.quote)
        assert span.content_hash == sha256(span.quote.encode("utf-8")).hexdigest()


def test_p2_examples_match_their_canonical_records() -> None:
    example_source = LegalSourceRecord.model_validate_json(
        (EXAMPLES / "legal-source-record.json").read_bytes()
    )
    canonical_source = LegalSourceRecord.model_validate_json(
        (CANONICAL_RULES / "legal_sources.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert example_source == canonical_source

    example_provision = ProvisionRecord.model_validate_json(
        (EXAMPLES / "provision-record.json").read_bytes()
    )
    canonical_provisions = [
        ProvisionRecord.model_validate_json(line)
        for line in (CANONICAL_RULES / "provisions.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert example_provision == next(
        item for item in canonical_provisions if item.provision_id == example_provision.provision_id
    )

    example_rule = RuleRecord.model_validate_json((EXAMPLES / "rule-record.json").read_bytes())
    canonical_rules = [
        RuleRecord.model_validate_json(line)
        for line in (CANONICAL_RULES / "rules.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert example_rule == next(
        item for item in canonical_rules if item.rule_id == example_rule.rule_id
    )
