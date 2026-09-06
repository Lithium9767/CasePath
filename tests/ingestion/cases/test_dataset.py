from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from casepath.contracts import CaseIndexRecord, CaseRecord, DecisionStatus, MaturityLevel
from casepath.ingestion.cases.build import PINNED_GENERATED_ON, build_dataset
from casepath.ingestion.cases.ids import (
    EXPECTED_CANONICAL_OUTPUT_HASHES,
    EXPECTED_CIVIL_CASE_COUNT,
    GYM_CASE_ID,
    PROPERTY_CASE_ID,
    REVIEWED_L2_CASE_IDS,
)
from casepath.ingestion.cases.validation import validate_canonical_cases
from casepath.ingestion.laws.civil_code import provision_id
from casepath.ingestion.laws.jsonl import read_jsonl, sha256_file, write_json
from casepath.rule_layer.ids import RULE_SERVICE_TERMINATION_REFUND

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
STATIC_DATA_ROOT = REPOSITORY_ROOT / "data"
UPSTREAM_CASES = REPOSITORY_ROOT.parent / "legal-rag" / "data" / "processed" / "processed_cases.json"
L1_PATH = Path("canonical/cases/cases_l1.jsonl")
DEEP_PATH = Path("canonical/cases/cases_deep.jsonl")
MANIFEST_PATH = Path("manifests/cases.manifest.json")


def _has_static_dataset() -> bool:
    return all(
        (STATIC_DATA_ROOT / path).is_file()
        for path in (L1_PATH, DEEP_PATH, MANIFEST_PATH, Path("canonical/rules/provisions.jsonl"))
    )


@pytest.fixture(scope="module")
def canonical_data_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if _has_static_dataset():
        return STATIC_DATA_ROOT
    if not UPSTREAM_CASES.is_file():
        pytest.skip("neither static P3 data nor the external legal-rag inputs are available")
    data_root = tmp_path_factory.mktemp("p3-dataset") / "data"
    shutil.copytree(STATIC_DATA_ROOT / "canonical" / "rules", data_root / "canonical" / "rules")
    build_dataset(UPSTREAM_CASES, data_root, generated_on=PINNED_GENERATED_ON)
    return data_root


def _read_cases(data_root: Path) -> tuple[list[CaseIndexRecord], list[CaseRecord]]:
    cases_root = data_root / "canonical" / "cases"
    return (
        read_jsonl(cases_root / "cases_l1.jsonl", CaseIndexRecord),
        read_jsonl(cases_root / "cases_deep.jsonl", CaseRecord),
    )


def test_all_l1_records_match_case_index_record(canonical_data_root: Path) -> None:
    lightweight, _ = _read_cases(canonical_data_root)
    assert len(lightweight) == EXPECTED_CIVIL_CASE_COUNT
    assert all(record.contract_version == "1.3" for record in lightweight)
    assert all(record.maturity == MaturityLevel.L1 for record in lightweight)


def test_all_civil_code_provision_ids_resolve(canonical_data_root: Path) -> None:
    report = validate_canonical_cases(canonical_data_root)
    lightweight, _ = _read_cases(canonical_data_root)
    cited = [pid for record in lightweight for pid in record.related_provision_ids]
    assert cited
    assert all(pid.startswith("law.prc.civil_code.2021.article_") for pid in cited)
    assert report.resolved_civil_code_citation_count == len(cited)
    assert provision_id(509) in cited
    assert all("provision.civil_code." not in pid for pid in cited)


def test_property_damages_case_is_not_in_refund_deep_set(canonical_data_root: Path) -> None:
    _, deep = _read_cases(canonical_data_root)
    deep_ids = {record.case_id for record in deep}
    assert PROPERTY_CASE_ID not in deep_ids
    assert all(
        RULE_SERVICE_TERMINATION_REFUND not in claim.invoked_rule_ids
        or record.case_id != PROPERTY_CASE_ID
        for record in deep
        for claim in record.claims
    )


def test_machine_bridges_and_unreviewed_cases_stay_l1(canonical_data_root: Path) -> None:
    _, deep = _read_cases(canonical_data_root)
    assert deep
    for record in deep:
        if record.case_id in REVIEWED_L2_CASE_IDS:
            continue
        assert record.maturity == MaturityLevel.L1
        assert all(item.human_verified is False for item in record.condition_findings)


def test_gym_judgment_extracts_partial_grant_and_amount(canonical_data_root: Path) -> None:
    _, deep = _read_cases(canonical_data_root)
    gym = next(record for record in deep if record.case_id == GYM_CASE_ID)
    assert gym.claims[0].amount == 24560
    assert gym.claims[0].invoked_rule_ids == [RULE_SERVICE_TERMINATION_REFUND]
    assert gym.decisions[0].status == DecisionStatus.PARTIALLY_GRANTED
    assert gym.decisions[0].amount == 16370
    assert gym.findings
    assert all("诉称" not in finding.predicate for finding in gym.findings)
    assert all("辩称" not in finding.predicate for finding in gym.findings)


def test_child_entity_ids_are_unique_across_cases(canonical_data_root: Path) -> None:
    _, deep = _read_cases(canonical_data_root)
    ids = []
    for record in deep:
        ids.extend(claim.claim_id for claim in record.claims)
        ids.extend(finding.finding_id for finding in record.findings)
        ids.extend(step.reasoning_id for step in record.reasoning_steps)
        ids.extend(decision.decision_id for decision in record.decisions)
    assert len(ids) == len(set(ids))
    assert "claim.primary" not in ids
    assert "finding.facts" not in ids
    assert "decision.primary" not in ids


def test_manifest_tamper_of_input_or_output_fails(canonical_data_root: Path, tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(canonical_data_root, copied)
    output_path = copied / "canonical" / "cases" / "cases_l1.jsonl"
    output_path.write_bytes(output_path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="manifest hash mismatch"):
        validate_canonical_cases(copied)

    copied_input = tmp_path / "input-tamper" / "data"
    shutil.copytree(canonical_data_root, copied_input)
    manifest_path = copied_input / "manifests" / "cases.manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["inputs"][0]["sha256"] = "0" * 64
    write_json(manifest_path, payload)
    with pytest.raises(ValueError, match="manifest input metadata mismatch"):
        validate_canonical_cases(copied_input)


@pytest.mark.skipif(not UPSTREAM_CASES.is_file(), reason="external legal-rag checkout is absent")
def test_rebuilds_are_byte_identical_and_match_canonical(tmp_path: Path) -> None:
    if not (STATIC_DATA_ROOT / "canonical" / "rules" / "provisions.jsonl").is_file():
        pytest.skip("P2 canonical provisions are required to rebuild P3")
    roots = [tmp_path / "first" / "data", tmp_path / "second" / "data"]
    for data_root in roots:
        shutil.copytree(STATIC_DATA_ROOT / "canonical" / "rules", data_root / "canonical" / "rules")
        build_dataset(UPSTREAM_CASES, data_root, generated_on=PINNED_GENERATED_ON)
    for relative in (L1_PATH, DEEP_PATH, MANIFEST_PATH):
        assert (roots[0] / relative).read_bytes() == (roots[1] / relative).read_bytes()
        if _has_static_dataset():
            assert (roots[0] / relative).read_bytes() == (STATIC_DATA_ROOT / relative).read_bytes()
    if EXPECTED_CANONICAL_OUTPUT_HASHES:
        assert sha256_file(roots[0] / L1_PATH) == EXPECTED_CANONICAL_OUTPUT_HASHES["cases_l1.jsonl"]


def test_public_validator_accepts_canonical_dataset(canonical_data_root: Path) -> None:
    report = validate_canonical_cases(canonical_data_root)
    assert report.status == "passed"
    assert report.l1_case_count == EXPECTED_CIVIL_CASE_COUNT
    assert report.deep_case_count >= 1
