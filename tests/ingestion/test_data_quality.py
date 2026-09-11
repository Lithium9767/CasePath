"""Quality metadata catches structural drift without exporting case text or merging rules."""

import json
from copy import deepcopy

import pytest

from casepath.contracts import (
    CaseIndexRecord,
    CaseRecord,
    LegalSourceRecord,
    ProvisionRecord,
    RuleRecord,
    SourceSpan,
)
from casepath.ingestion import data_quality
from casepath.ingestion.laws.civil_code import CIVIL_CODE_SOURCE_ID, provision_id
from casepath.ingestion.laws.jsonl import sha256_file, write_json, write_jsonl


def corpus():
    source = LegalSourceRecord(source_id=CIVIL_CODE_SOURCE_ID, title="Test law", source_type="LAW")
    spans = [
        SourceSpan(
            span_id=f"span.test.{number}",
            source_id=source.source_id,
            section=f"第{number}条",
            start_offset=0,
            end_offset=2,
            quote="AB",
        )
        for number in (1, 2)
    ]
    provisions = [
        ProvisionRecord(
            provision_id=provision_id(number),
            source_id=source.source_id,
            article_no=str(number),
            title=f"Article {number}",
            text="AB",
            source_spans=[spans[number - 1]],
        )
        for number in (1, 2)
    ]
    rule = RuleRecord(
        rule_id="rule.test.one",
        title="Test",
        claim_types=["Test"],
        maturity="L1",
        provisions=[
            {
                "source_id": source.source_id,
                "provision_id": provision_id(1),
                "article_no": "1",
                "title": "Article 1",
            }
        ],
        conditions=[
            {
                "condition_id": "cond.test.one",
                "label": "Test",
                "predicate": "Test predicate",
                "source_span_ids": [spans[0].span_id],
            }
        ],
        condition_groups=[
            {
                "group_id": "group.test.one",
                "label": "Test",
                "role": "CORE",
                "operator": "ALL",
                "member_condition_ids": ["cond.test.one"],
            }
        ],
        source_spans=[spans[0]],
    )
    return [source], provisions, [rule], spans, [], []


def audit(values):
    return data_quality.audit_records(*values, expected_article_count=2)


def codes(report):
    return {item["code"] for item in report["errors"]}


def test_duplicate_predicate_and_missing_diagnostic_source_are_not_failures():
    values = corpus()
    payload = values[2][0].model_dump(mode="json")
    payload["rule_id"] = "rule.test.two"
    payload["conditions"][0]["condition_id"] = "cond.test.two"
    payload["condition_groups"][0]["member_condition_ids"] = ["cond.test.two"]
    payload["conditions"].append(
        {
            "condition_id": "cond.test.diagnostic",
            "label": "Context",
            "predicate": "Context",
            "source_span_ids": [],
        }
    )
    payload["condition_groups"].append(
        {
            "group_id": "group.diagnostic",
            "label": "Context",
            "role": "DIAGNOSTIC",
            "operator": "ALL",
            "member_condition_ids": ["cond.test.diagnostic"],
        }
    )
    values[2].append(RuleRecord.model_validate(payload))
    report = audit(values)
    assert report["status"] == "passed"
    assert len(report["duplicate_condition_predicates"]) == 1
    assert len(report["duplicate_condition_definitions"]) == 1
    assert len(report["diagnostic_conditions_without_source"]) == 1
    assert report["article_coverage"][0]["rule_referenced_articles"] == 1
    assert report["counts"]["unique_condition_ids"] == 3
    assert "Test predicate" not in json.dumps(report)


def test_shared_id_checks_full_definition_including_evidence_types():
    values = corpus()
    payload = values[2][0].model_dump(mode="json")
    payload["rule_id"] = "rule.test.two"
    payload["conditions"][0]["evidence_types"] = ["different evidence"]
    values[2].append(RuleRecord.model_validate(payload))
    report = audit(values)
    assert report["status"] == "failed"
    assert "shared_entity_definition_conflict" in codes(report)


def test_span_offset_and_undeclared_parent_provision_are_failures():
    values = corpus()
    values[3][1].end_offset = 1
    values[2][0].source_spans.append(values[3][1])
    report = audit(values)
    assert {"law_span_quote_offset_mismatch", "span_parent_provision_not_declared"} <= codes(report)
    assert report["parent_reference_omissions"] == [
        {"rule_id": "rule.test.one", "span_id": "span.test.2", "provision_id": provision_id(2)}
    ]


def test_case_bridge_and_reasoning_references_are_checked_without_exporting_names():
    values = corpus()
    secret = "Private person and full case text"
    span = SourceSpan(
        span_id="case.span.one",
        source_id="case.secret",
        section="裁判理由",
        start_offset=0,
        end_offset=len(secret),
        quote=secret,
    )
    values[4].append(CaseIndexRecord(case_id="case.secret", title=secret, reasoning_text=secret))
    values[5].append(
        CaseRecord(
            case_id="case.secret",
            title=secret,
            maturity="L1",
            claims=[{"claim_id": "claim.one", "claim_type": "Test", "requested_remedy": secret}],
            condition_findings=[
                {"condition_id": "cond.unknown", "status": "UNKNOWN", "confidence": 0}
            ],
            reasoning_steps=[
                {
                    "reasoning_id": "reason.one",
                    "applied_rule_ids": ["rule.unknown"],
                    "conclusion": secret,
                    "source_span_ids": [span.span_id],
                }
            ],
            source_spans=[span],
        )
    )
    report = audit(values)
    assert {"existing_case_record_validator_failed", "case_reasoning_unknown_rule"} <= codes(report)
    assert secret not in json.dumps(report)
    assert "case.secret" not in json.dumps(report)


def test_empty_bodies_and_duplicate_case_ids_fail():
    values = corpus()
    values[1][0].text = " "
    case = CaseIndexRecord(case_id="private-case-id", title="Private name")
    values[4].extend([case, case.model_copy(deep=True)])
    report = audit(values)
    assert {"empty_provision_text", "empty_case_text", "duplicate_id"} <= codes(report)
    assert "private-case-id" not in json.dumps(report)


def write_corpus(root):
    values = corpus()
    for relative, records in zip(data_quality.FILES, values, strict=True):
        write_jsonl(root / relative, records)
    for relative in data_quality.MANIFESTS:
        write_json(root / relative, {"fixture": True})


def test_cli_binds_all_files_and_fails_for_incomplete_article_sequence(tmp_path, capsys):
    root = tmp_path / "data"
    output = root / "metadata" / "quality.json"
    write_corpus(root)
    before = {
        name: sha256_file(root / name) for name in (*data_quality.FILES, *data_quality.MANIFESTS)
    }
    assert data_quality.main(["--data-root", str(root), "--output", str(output)]) == 1
    report = json.loads(output.read_text("utf-8"))
    assert report["generated_on"] == "2026-09-11"
    assert "article_sequence" in codes(report)
    assert {name: item["sha256"] for name, item in report["files"].items()} == before
    assert before == {name: sha256_file(root / name) for name in before}
    assert json.loads(capsys.readouterr().out) == report
    assert data_quality.build_report(root) == report


def test_invalid_record_does_not_leak_pydantic_input(tmp_path):
    root = tmp_path / "data"
    write_corpus(root)
    write_jsonl(
        root / "canonical/cases/cases_l1.jsonl",
        [
            {
                "case_id": "private-person",
                "title": "Secret name",
                "facts_text": "Secret facts",
                "maturity": "invalid maturity",
            }
        ],
    )
    report = data_quality.build_report(root)
    assert "record_contract_validation_failed" in codes(report)
    rendered = json.dumps(report)
    assert "Secret" not in rendered
    assert "private-person" not in rendered


@pytest.mark.parametrize(
    "relative", ["canonical/rules/rules.jsonl", "manifests/report.json", "inputs/report.json"]
)
def test_output_cannot_overwrite_input_data(tmp_path, relative):
    root = tmp_path / "data"
    write_corpus(root)
    before = sha256_file(root / "canonical/rules/rules.jsonl")
    with pytest.raises(SystemExit) as result:
        data_quality.main(["--data-root", str(root), "--output", str(root / relative)])
    assert result.value.code == 2
    assert sha256_file(root / "canonical/rules/rules.jsonl") == before


def test_cli_returns_zero_for_passed_report(tmp_path, monkeypatch, capsys):
    report = audit(corpus())
    monkeypatch.setattr(data_quality, "build_report", lambda _: deepcopy(report))
    assert data_quality.main(["--data-root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "passed"
