from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest

from casepath.adapters.demo import DemoConditionProjector
from casepath.contracts import LegalSourceRecord, ProvisionRecord, RuleRecord, SourceSpan
from casepath.ingestion.laws.civil_code import EXPECTED_SOURCE_SHA256, provision_id
from casepath.ingestion.laws.jsonl import (
    read_jsonl,
    sha256_file,
    sha256_text,
    write_json,
    write_jsonl,
)
from casepath.ingestion.laws.manifest import CivilCodeManifest
from casepath.rule_layer.build import build_dataset
from casepath.rule_layer.ids import (
    COND_ALTERNATIVE_PERFORMANCE,
    COND_PERFORMANCE_IMPOSSIBLE,
    HUMAN_VERIFIED_L3_RULE_IDS,
    RULE_NONPERFORMANCE_TERMINATION,
    RULE_SERVICE_TERMINATION_REFUND,
)
from casepath.rule_layer.validation import (
    EXPECTED_ARTICLE_CONTENT_HASHES,
    EXPECTED_CANONICAL_OUTPUT_HASHES,
    validate_canonical_dataset,
    validate_records,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
STATIC_DATA_ROOT = REPOSITORY_ROOT / "data"
UPSTREAM_LAWS_ROOT = REPOSITORY_ROOT.parent / "legal-rag" / "data" / "laws"
UPSTREAM_SOURCE = UPSTREAM_LAWS_ROOT / "民法典_法条.json"
UPSTREAM_STATS = UPSTREAM_LAWS_ROOT / "民法典_统计.json"
EXPECTED_OUTPUTS = (
    Path("canonical/rules/legal_sources.jsonl"),
    Path("canonical/rules/provisions.jsonl"),
    Path("canonical/rules/rules.jsonl"),
    Path("canonical/rules/source_spans.jsonl"),
    Path("manifests/civil_code.manifest.json"),
)


def _has_complete_static_dataset() -> bool:
    return all((STATIC_DATA_ROOT / path).is_file() for path in EXPECTED_OUTPUTS)


@pytest.fixture(scope="module")
def canonical_data_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if _has_complete_static_dataset():
        return STATIC_DATA_ROOT
    if not UPSTREAM_SOURCE.is_file() or not UPSTREAM_STATS.is_file():
        pytest.skip("neither static P2 data nor the external legal-rag inputs are available")

    data_root = tmp_path_factory.mktemp("p2-dataset") / "data"
    build_dataset(
        source_path=UPSTREAM_SOURCE,
        stats_path=UPSTREAM_STATS,
        data_root=data_root,
        verified_on=date(2026, 9, 4),
        upstream_revision="ce7872c7ae343e5ff860d627195ec4e72c7ef7ce",
    )
    return data_root


def _read_dataset(
    data_root: Path,
) -> tuple[
    list[LegalSourceRecord],
    list[ProvisionRecord],
    list[RuleRecord],
    list[SourceSpan],
]:
    canonical_root = data_root / "canonical" / "rules"
    return (
        read_jsonl(canonical_root / "legal_sources.jsonl", LegalSourceRecord),
        read_jsonl(canonical_root / "provisions.jsonl", ProvisionRecord),
        read_jsonl(canonical_root / "rules.jsonl", RuleRecord),
        read_jsonl(canonical_root / "source_spans.jsonl", SourceSpan),
    )


def test_generated_dataset_passes_the_public_validator(canonical_data_root: Path) -> None:
    report = validate_canonical_dataset(canonical_data_root)

    assert report.status == "passed"
    assert report.legal_source_count == 1
    assert report.provision_count == 1260
    assert report.rule_count == 5
    assert report.l3_rule_count == 4
    assert report.source_span_count == 1268
    assert report.checked_article_numbers == [509, 563, 565, 566]


def test_static_jsonl_records_and_cross_references_are_complete(
    canonical_data_root: Path,
) -> None:
    legal_sources, provisions, rules, spans = _read_dataset(canonical_data_root)
    source_ids = {record.source_id for record in legal_sources}
    provision_by_id = {record.provision_id: record for record in provisions}
    span_by_id = {span.span_id: span for span in spans}

    assert {record.contract_version for record in legal_sources} == {"1.1"}
    assert len(source_ids) == 1
    assert len(provision_by_id) == 1260
    assert len(span_by_id) == 1268
    assert [int(record.article_no) for record in provisions] == list(range(1, 1261))

    for provision in provisions:
        assert provision.contract_version == "1.1"
        assert provision.source_id in source_ids
        assert provision.provision_id == provision_id(int(provision.article_no))
        assert provision.text
        assert provision.effective_from == date(2021, 1, 1)
        assert provision.effective_to is None
        assert len(provision.source_spans) == 1
        full_span = provision.source_spans[0]
        assert span_by_id[full_span.span_id] == full_span
        assert full_span.start_offset == 0
        assert full_span.end_offset == len(provision.text)
        assert full_span.quote == provision.text

    for rule in rules:
        assert rule.contract_version == "1.1"
        embedded_span_ids = {span.span_id for span in rule.source_spans}
        assert rule.source_spans
        for reference in rule.provisions:
            target = provision_by_id[reference.provision_id]
            assert reference.valid_from == target.effective_from
            assert reference.valid_to == target.effective_to
        referenced_span_ids = {
            span_id
            for item in [*rule.conditions, *rule.exceptions, *rule.consequences]
            for span_id in item.source_span_ids
        }
        assert referenced_span_ids <= embedded_span_ids
        assert referenced_span_ids <= span_by_id.keys()


def test_target_articles_have_padded_ids_and_fixed_verified_hashes(
    canonical_data_root: Path,
) -> None:
    _, provisions, _, _ = _read_dataset(canonical_data_root)
    by_number = {int(record.article_no): record for record in provisions}

    for article_number, expected_hash in EXPECTED_ARTICLE_CONTENT_HASHES.items():
        provision = by_number[article_number]
        assert provision.provision_id.endswith(f"article_{article_number:04d}")
        assert sha256_text(provision.text) == expected_hash


def test_rule_groups_cover_conditions_and_alternative_performance_is_an_exception(
    canonical_data_root: Path,
) -> None:
    _, _, rules, _ = _read_dataset(canonical_data_root)
    alternative_rule_ids = set()
    alternative_definitions = []

    for rule in rules:
        condition_ids = [condition.condition_id for condition in rule.conditions]
        grouped_ids = [
            condition_id
            for group in rule.condition_groups
            for condition_id in group.member_condition_ids
        ]
        assert len(rule.condition_groups) == 1
        assert rule.condition_groups[0].operator == "ALL"
        assert grouped_ids == condition_ids
        assert COND_ALTERNATIVE_PERFORMANCE not in condition_ids

        alternative_exceptions = [
            exception
            for exception in rule.exceptions
            if exception.exception_id == COND_ALTERNATIVE_PERFORMANCE
        ]
        if alternative_exceptions:
            alternative_rule_ids.add(rule.rule_id)
            alternative_definitions.extend(alternative_exceptions)

    assert alternative_rule_ids == {
        RULE_NONPERFORMANCE_TERMINATION,
        RULE_SERVICE_TERMINATION_REFUND,
    }
    assert len(alternative_definitions) == 2
    assert alternative_definitions[0] == alternative_definitions[1]


def test_demo_rule_and_condition_ids_remain_compatible(canonical_data_root: Path) -> None:
    _, _, rules, _ = _read_dataset(canonical_data_root)
    demo_rule = next(rule for rule in rules if rule.rule_id == RULE_SERVICE_TERMINATION_REFUND)
    condition_ids = {condition.condition_id for condition in demo_rule.conditions}
    exception_ids = {exception.exception_id for exception in demo_rule.exceptions}
    resolvable_ids = condition_ids | exception_ids

    assert COND_PERFORMANCE_IMPOSSIBLE == "cond.performance_impossible"
    assert COND_PERFORMANCE_IMPOSSIBLE in condition_ids
    assert "cond.performance.impossible" not in condition_ids
    assert set(DemoConditionProjector.CONDITION_IDS) <= resolvable_ids
    assert set(DemoConditionProjector.CONDITION_IDS[:-1]) <= condition_ids
    assert DemoConditionProjector.CONDITION_IDS[-1] == COND_ALTERNATIVE_PERFORMANCE
    assert COND_ALTERNATIVE_PERFORMANCE not in condition_ids
    assert COND_ALTERNATIVE_PERFORMANCE in exception_ids


def test_every_source_span_uses_right_open_offsets(canonical_data_root: Path) -> None:
    _, provisions, _, spans = _read_dataset(canonical_data_root)
    provision_by_number = {int(record.article_no): record for record in provisions}

    for span in spans:
        article_number = int((span.section or "").removeprefix("第").removesuffix("条"))
        text = provision_by_number[article_number].text
        assert 0 <= span.start_offset < span.end_offset <= len(text)
        assert text[span.start_offset : span.end_offset] == span.quote


def test_validator_rejects_drifted_rule_references_and_condition_definitions(
    canonical_data_root: Path,
) -> None:
    legal_sources, provisions, rules, spans = _read_dataset(canonical_data_root)
    rules[0].provisions[0].title = "被篡改的法条标题"

    with pytest.raises(ValueError, match="inconsistent ProvisionRef"):
        validate_records(legal_sources, provisions, rules, spans)

    legal_sources, provisions, rules, spans = _read_dataset(canonical_data_root)
    repeated_condition = next(
        condition
        for rule in rules
        if rule.rule_id == RULE_SERVICE_TERMINATION_REFUND
        for condition in rule.conditions
        if condition.condition_id == "cond.contract_exists"
    )
    repeated_condition.label = "冲突定义"

    with pytest.raises(ValueError, match="conflicting definitions"):
        validate_records(legal_sources, provisions, rules, spans)


def test_validator_rechecks_group_coverage_and_alternative_exception(
    canonical_data_root: Path,
) -> None:
    legal_sources, provisions, rules, spans = _read_dataset(canonical_data_root)
    rules[0].condition_groups[0].member_condition_ids.pop()
    with pytest.raises(ValueError, match="not assigned to a group"):
        validate_records(legal_sources, provisions, rules, spans)

    legal_sources, provisions, rules, spans = _read_dataset(canonical_data_root)
    target_rule = next(rule for rule in rules if rule.rule_id == RULE_NONPERFORMANCE_TERMINATION)
    target_rule.exceptions.clear()
    with pytest.raises(ValueError, match="alternative-performance exception"):
        validate_records(legal_sources, provisions, rules, spans)


def test_validator_rejects_source_metadata_span_source_and_empty_l3_evidence(
    canonical_data_root: Path,
) -> None:
    legal_sources, provisions, rules, spans = _read_dataset(canonical_data_root)
    legal_sources[0].authority = "错误机关"
    with pytest.raises(ValueError, match="metadata differs"):
        validate_records(legal_sources, provisions, rules, spans)

    legal_sources, provisions, rules, spans = _read_dataset(canonical_data_root)
    spans[0].source_id = "law.invalid"
    with pytest.raises(ValueError, match="source_id differs"):
        validate_records(legal_sources, provisions, rules, spans)

    legal_sources, provisions, rules, spans = _read_dataset(canonical_data_root)
    rules[0].conditions[0].evidence_types = []
    with pytest.raises(ValueError, match="has no evidence types"):
        validate_records(legal_sources, provisions, rules, spans)


def test_validator_binds_review_status_to_exact_rule_semantics(
    canonical_data_root: Path,
) -> None:
    legal_sources, provisions, rules, spans = _read_dataset(canonical_data_root)
    rules[0].consequences[0].description = "结构仍合法、但未经复核的规则语义"

    with pytest.raises(ValueError, match="reviewed deterministic generator output"):
        validate_records(legal_sources, provisions, rules, spans)


def test_manifest_output_paths_are_bound_to_the_canonical_files(
    canonical_data_root: Path,
    tmp_path: Path,
) -> None:
    copied_data_root = tmp_path / "data"
    shutil.copytree(canonical_data_root, copied_data_root)
    manifest_path = copied_data_root / "manifests" / "civil_code.manifest.json"
    manifest = CivilCodeManifest.model_validate_json(manifest_path.read_bytes())
    manifest.outputs[0].path = "data/shadow/legal_sources.jsonl"
    write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="manifest output list"):
        validate_canonical_dataset(copied_data_root)


def test_validator_rejects_self_consistent_changes_to_the_pinned_corpus(
    canonical_data_root: Path,
    tmp_path: Path,
) -> None:
    copied_data_root = tmp_path / "data"
    shutil.copytree(canonical_data_root, copied_data_root)
    legal_sources, provisions, rules, spans = _read_dataset(copied_data_root)

    provision = provisions[0]
    provision.text += "篡改"
    embedded_full_span = provision.source_spans[0]
    embedded_full_span.quote = provision.text
    embedded_full_span.end_offset = len(provision.text)
    standalone_full_span = next(
        span for span in spans if span.span_id == embedded_full_span.span_id
    )
    standalone_full_span.quote = provision.text
    standalone_full_span.end_offset = len(provision.text)

    canonical_root = copied_data_root / "canonical" / "rules"
    write_jsonl(canonical_root / "legal_sources.jsonl", legal_sources)
    write_jsonl(canonical_root / "provisions.jsonl", provisions)
    write_jsonl(canonical_root / "rules.jsonl", rules)
    write_jsonl(canonical_root / "source_spans.jsonl", spans)
    manifest_path = copied_data_root / "manifests" / "civil_code.manifest.json"
    manifest = CivilCodeManifest.model_validate_json(manifest_path.read_bytes())
    for output in manifest.outputs:
        output.sha256 = sha256_file(copied_data_root.parent / output.path)
    write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="pinned release hash mismatch"):
        validate_canonical_dataset(copied_data_root)


def test_manifest_pins_inputs_repairs_reviews_and_output_hashes(
    canonical_data_root: Path,
) -> None:
    manifest_path = canonical_data_root / "manifests" / "civil_code.manifest.json"
    manifest = CivilCodeManifest.model_validate_json(manifest_path.read_bytes())
    _, _, rules, _ = _read_dataset(canonical_data_root)

    source_input = next(item for item in manifest.inputs if item.path.endswith("民法典_法条.json"))
    assert {item.path for item in manifest.inputs} == {
        "../legal-rag/data/laws/民法典_法条.json",
        "../legal-rag/data/laws/民法典_统计.json",
    }
    assert source_input.sha256 == EXPECTED_SOURCE_SHA256
    assert source_input.record_count == 1260
    assert manifest.transformations[0].affected_records == 109
    assert manifest.transformations[0].guard_sha256 == EXPECTED_SOURCE_SHA256
    assert manifest.rule_review_status == {
        rule.rule_id: (
            "human_verified"
            if rule.rule_id in HUMAN_VERIFIED_L3_RULE_IDS
            else "needs_additional_authority"
        )
        for rule in rules
    }
    assert set(manifest.authority_verification.checked_article_numbers) == {
        509,
        563,
        565,
        566,
    }

    manifest_root = canonical_data_root.parent
    for output in manifest.outputs:
        output_path = manifest_root / output.path
        assert output_path.is_file()
        assert sha256_file(output_path) == output.sha256
        assert output.sha256 == EXPECTED_CANONICAL_OUTPUT_HASHES[output_path.name]


@pytest.mark.skipif(
    not UPSTREAM_SOURCE.is_file() or not UPSTREAM_STATS.is_file(),
    reason="external legal-rag checkout is absent",
)
def test_repeated_builds_are_byte_for_byte_deterministic(tmp_path: Path) -> None:
    roots = [tmp_path / "first" / "data", tmp_path / "second" / "data"]
    for data_root in roots:
        build_dataset(
            source_path=UPSTREAM_SOURCE,
            stats_path=UPSTREAM_STATS,
            data_root=data_root,
            verified_on=date(2026, 9, 4),
            upstream_revision="ce7872c7ae343e5ff860d627195ec4e72c7ef7ce",
        )

    for relative_path in EXPECTED_OUTPUTS:
        assert (roots[0] / relative_path).read_bytes() == (roots[1] / relative_path).read_bytes()
