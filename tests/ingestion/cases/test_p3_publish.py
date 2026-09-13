from __future__ import annotations

from pathlib import Path
from shutil import copy2, copytree

import pytest

import casepath.ingestion.cases.build as build_module
from casepath.ingestion.cases.build import PINNED_GENERATED_ON, build_dataset
from casepath.ingestion.cases.spc_supplement import INPUT_PATH as SPC_INPUT_PATH
from casepath.ingestion.laws.jsonl import sha256_file

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
STATIC_DATA_ROOT = REPOSITORY_ROOT / "data"
UPSTREAM_CASES = (
    REPOSITORY_ROOT.parent / "legal-rag" / "data" / "processed" / "processed_cases.json"
)
PUBLISHED = (
    Path("canonical/cases/cases_l1.jsonl"),
    Path("canonical/cases/cases_deep.jsonl"),
    Path("manifests/cases.manifest.json"),
)


def _seed_published_files(data_root: Path) -> dict[str, bytes]:
    rules_root = STATIC_DATA_ROOT / "canonical" / "rules"
    if not (rules_root / "provisions.jsonl").is_file():
        pytest.skip("P2 canonical provisions are required to exercise the P3 publisher")
    copytree(rules_root, data_root / "canonical" / "rules")
    (data_root / "canonical" / "cases").mkdir(parents=True)
    (data_root / "manifests").mkdir()
    payloads = {
        PUBLISHED[0]: b"old-l1\n",
        PUBLISHED[1]: b"old-deep\n",
        PUBLISHED[2]: b"old-manifest\n",
    }
    for relative_path, content in payloads.items():
        (data_root / relative_path).write_bytes(content)
    return {relative.as_posix(): (data_root / relative).read_bytes() for relative in PUBLISHED}


def _published_hashes(data_root: Path) -> dict[str, str]:
    return {relative.as_posix(): sha256_file(data_root / relative) for relative in PUBLISHED}


@pytest.mark.skipif(not UPSTREAM_CASES.is_file(), reason="external legal-rag checkout is absent")
def test_late_validation_failure_does_not_change_published_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    before = _seed_published_files(data_root)
    before_hashes = _published_hashes(data_root)

    def fail_staged_validation(_data_root: Path) -> None:
        raise RuntimeError("simulated staged validation failure")

    monkeypatch.setattr(build_module, "validate_canonical_cases", fail_staged_validation)

    with pytest.raises(RuntimeError, match="simulated staged validation failure"):
        build_dataset(UPSTREAM_CASES, data_root, generated_on=PINNED_GENERATED_ON)

    after = {relative.as_posix(): (data_root / relative).read_bytes() for relative in PUBLISHED}
    assert after == before
    assert _published_hashes(data_root) == before_hashes
    assert not list(tmp_path.glob(f".{data_root.name}.staging-*"))
    assert not list(tmp_path.glob(f".{data_root.name}.backup-*"))


@pytest.mark.skipif(not UPSTREAM_CASES.is_file(), reason="external legal-rag checkout is absent")
def test_input_changed_after_staged_validation_does_not_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    before = _seed_published_files(data_root)
    before_hashes = _published_hashes(data_root)
    actual_source = REPOSITORY_ROOT / SPC_INPUT_PATH
    actual_source_hash = sha256_file(actual_source)
    temporary_source = data_root / "inputs" / actual_source.name
    temporary_source.parent.mkdir()
    copy2(actual_source, temporary_source)
    assert temporary_source.resolve().is_relative_to(tmp_path.resolve())
    assert build_module._spc_input_path(data_root).resolve() == temporary_source.resolve()
    original_validate = build_module.validate_canonical_cases
    staged_validation_succeeded = False

    def validate_then_change_temporary_input(staged_data_root: Path):
        nonlocal staged_validation_succeeded
        report = original_validate(staged_data_root)
        assert report.status == "passed"
        staged_validation_succeeded = True
        # Simulate a concurrent edit only to the copied test input, never to
        # the repository source or the external legal-rag checkout.
        temporary_source.write_bytes(temporary_source.read_bytes() + b"\n")
        return report

    monkeypatch.setattr(
        build_module, "validate_canonical_cases", validate_then_change_temporary_input
    )
    with pytest.raises(ValueError, match="case input changed during build; refusing to publish"):
        build_dataset(UPSTREAM_CASES, data_root, generated_on=PINNED_GENERATED_ON)

    assert staged_validation_succeeded
    assert {
        relative.as_posix(): (data_root / relative).read_bytes() for relative in PUBLISHED
    } == before
    assert _published_hashes(data_root) == before_hashes
    assert sha256_file(actual_source) == actual_source_hash
    assert not list(tmp_path.glob(f".{data_root.name}.staging-*"))
    assert not list(tmp_path.glob(f".{data_root.name}.backup-*"))
