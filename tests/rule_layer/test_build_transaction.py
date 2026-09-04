from __future__ import annotations

import os
import subprocess
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import casepath.rule_layer.build as build_module
import casepath.rule_layer.validation as validation_module
from casepath.ingestion.laws.civil_code import EXPECTED_UPSTREAM_REVISION


def _seed_existing_dataset(data_root: Path) -> dict[str, bytes]:
    generated_root = data_root / "canonical" / "rules"
    generated_root.mkdir(parents=True)
    (generated_root / "rules.jsonl").write_bytes(b"old rules\n")
    (data_root / "manifests").mkdir()
    (data_root / "manifests" / "civil_code.manifest.json").write_bytes(b"old manifest\n")
    (generated_root / "README.md").write_bytes(b"hand-written documentation\n")
    (data_root / "p3").mkdir()
    (data_root / "p3" / "sentinel.json").write_bytes(b"unrelated concurrent output\n")
    return _snapshot(data_root)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _assert_no_transaction_directories(parent: Path, data_root_name: str) -> None:
    assert not list(parent.glob(f".{data_root_name}.staging-*"))
    assert not list(parent.glob(f".{data_root_name}.backup-*"))


def _patch_minimal_build_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    conversion = SimpleNamespace(
        legal_source={"source_id": "source.test"},
        provisions=[],
        source_spans=[],
        hierarchy_repair_count=0,
    )
    rule_build = SimpleNamespace(rules=[], source_spans=[])
    monkeypatch.setattr(build_module, "_validate_stats", lambda _path: 1260)
    monkeypatch.setattr(build_module, "load_civil_code", lambda _path: object())
    monkeypatch.setattr(build_module, "convert_civil_code", lambda _raw: conversion)
    monkeypatch.setattr(build_module, "build_civil_code_rules", lambda _provisions: rule_build)
    monkeypatch.setattr(validation_module, "validate_records", lambda *_args: None)


def _write_staged_outputs(staged_data_root: Path) -> None:
    staged_data_root.mkdir()
    for relative_path in build_module.GENERATED_PATHS:
        staged_path = staged_data_root / relative_path
        staged_path.parent.mkdir(parents=True, exist_ok=True)
        staged_path.write_bytes(f"replacement for {relative_path.name}\n".encode())


def test_git_revision_failure_does_not_change_existing_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    before = _seed_existing_dataset(data_root)
    source_path = tmp_path / "source.json"
    stats_path = tmp_path / "stats.json"
    source_path.write_text("{}", encoding="utf-8")
    stats_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(build_module, "_validate_stats", lambda _path: 1260)

    def fail_revision(_source_path: Path) -> str:
        raise subprocess.CalledProcessError(128, ["git", "rev-parse", "HEAD"])

    monkeypatch.setattr(build_module, "_git_revision", fail_revision)

    with pytest.raises(subprocess.CalledProcessError):
        build_module.build_dataset(
            source_path=source_path,
            stats_path=stats_path,
            data_root=data_root,
            verified_on=date(2026, 9, 4),
        )

    assert _snapshot(data_root) == before
    _assert_no_transaction_directories(tmp_path, data_root.name)


def test_unpinned_revision_is_rejected_before_any_output_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    source_path = tmp_path / "source.json"
    stats_path = tmp_path / "stats.json"
    source_path.write_text("{}", encoding="utf-8")
    stats_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(build_module, "_validate_stats", lambda _path: 1260)

    with pytest.raises(ValueError, match="unexpected upstream revision"):
        build_module.build_dataset(
            source_path=source_path,
            stats_path=stats_path,
            data_root=data_root,
            verified_on=date(2026, 9, 4),
            upstream_revision="0" * 40,
        )

    assert not data_root.exists()


def test_late_staged_validation_failure_does_not_change_existing_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    before = _seed_existing_dataset(data_root)
    source_path = tmp_path / "source.json"
    stats_path = tmp_path / "stats.json"
    source_path.write_text("{}", encoding="utf-8")
    stats_path.write_text("{}", encoding="utf-8")

    _patch_minimal_build_inputs(monkeypatch)

    def fail_staged_validation(_data_root: Path) -> None:
        raise RuntimeError("simulated staged validation failure")

    monkeypatch.setattr(build_module, "validate_canonical_dataset", fail_staged_validation)

    with pytest.raises(RuntimeError, match="simulated staged validation failure"):
        build_module.build_dataset(
            source_path=source_path,
            stats_path=stats_path,
            data_root=data_root,
            verified_on=date(2026, 9, 4),
            upstream_revision=EXPECTED_UPSTREAM_REVISION,
        )

    assert _snapshot(data_root) == before
    _assert_no_transaction_directories(tmp_path, data_root.name)


def test_publication_failure_rolls_back_existing_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    before = _seed_existing_dataset(data_root)
    staged_data_root = tmp_path / "staged-data"
    _write_staged_outputs(staged_data_root)
    failed_relative_path = Path("canonical/rules/rules.jsonl")
    original_replace = Path.replace

    def fail_staged_promotion(path: Path, target: Path) -> Path:
        if (
            path == staged_data_root / failed_relative_path
            and target == data_root / failed_relative_path
        ):
            raise OSError("simulated directory promotion failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_staged_promotion)

    with pytest.raises(OSError, match="simulated directory promotion failure"):
        build_module._publish_staged_outputs(staged_data_root, data_root)

    assert _snapshot(data_root) == before
    assert (staged_data_root / failed_relative_path).is_file()
    _assert_no_transaction_directories(tmp_path, data_root.name)


def test_rollback_failure_preserves_recovery_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _seed_existing_dataset(data_root)
    staged_data_root = tmp_path / "staged-data"
    _write_staged_outputs(staged_data_root)
    failed_relative_path = Path("canonical/rules/rules.jsonl")
    rollback_failure_path = data_root / "canonical" / "rules" / "legal_sources.jsonl"
    original_replace = Path.replace
    original_unlink = Path.unlink

    def fail_staged_promotion(path: Path, target: Path) -> Path:
        if (
            path == staged_data_root / failed_relative_path
            and target == data_root / failed_relative_path
        ):
            raise OSError("simulated publication failure")
        return original_replace(path, target)

    def fail_one_rollback(path: Path, missing_ok: bool = False) -> None:
        if path == rollback_failure_path:
            raise OSError("simulated rollback failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "replace", fail_staged_promotion)
    monkeypatch.setattr(Path, "unlink", fail_one_rollback)

    with pytest.raises(RuntimeError, match="unrecovered backups remain") as error:
        build_module._publish_staged_outputs(staged_data_root, data_root)

    backup_containers = list(tmp_path.glob(".data.backup-*"))
    assert len(backup_containers) == 1
    backup_data_root = backup_containers[0] / data_root.name
    assert str(backup_data_root) in str(error.value)
    assert (backup_data_root / "canonical" / "rules" / "rules.jsonl").read_bytes() == (
        b"old rules\n"
    )
    assert (
        backup_data_root / "manifests" / "civil_code.manifest.json"
    ).read_bytes() == b"old manifest\n"


def test_successful_publication_preserves_non_generated_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _seed_existing_dataset(data_root)
    source_path = tmp_path / "source.json"
    stats_path = tmp_path / "stats.json"
    source_path.write_text("{}", encoding="utf-8")
    stats_path.write_text("{}", encoding="utf-8")
    _patch_minimal_build_inputs(monkeypatch)
    validation = validation_module.DatasetValidationReport(
        legal_source_count=1,
        provision_count=0,
        rule_count=0,
        l3_rule_count=0,
        source_span_count=0,
        checked_article_numbers=[],
        status="passed",
    )
    monkeypatch.setattr(
        build_module,
        "validate_canonical_dataset",
        lambda _data_root: validation,
    )

    result = build_module.build_dataset(
        source_path=source_path,
        stats_path=stats_path,
        data_root=data_root,
        verified_on=date(2026, 9, 4),
        upstream_revision=EXPECTED_UPSTREAM_REVISION,
    )

    assert result.validation == validation
    assert result.manifest_path == data_root / "manifests" / "civil_code.manifest.json"
    assert (data_root / "canonical" / "rules" / "README.md").read_bytes() == (
        b"hand-written documentation\n"
    )
    assert (data_root / "p3" / "sentinel.json").read_bytes() == (b"unrelated concurrent output\n")
    assert (data_root / "canonical" / "rules" / "rules.jsonl").read_bytes() == b""
    _assert_no_transaction_directories(tmp_path, data_root.name)


@pytest.mark.parametrize(
    "protected_root",
    [build_module.PROJECT_ROOT, build_module.PROJECT_ROOT.parent, Path.home()],
)
def test_build_refuses_protected_data_roots(protected_root: Path) -> None:
    with pytest.raises(ValueError, match="protected data_root"):
        build_module.build_dataset(
            source_path=Path("unused-source.json"),
            stats_path=Path("unused-stats.json"),
            data_root=protected_root,
            verified_on=date(2026, 9, 4),
            upstream_revision=EXPECTED_UPSTREAM_REVISION,
        )


def test_build_refuses_symbolic_link_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    output_path = data_root / "canonical" / "rules" / "rules.jsonl"
    output_path.parent.mkdir(parents=True)
    link_target = tmp_path / "outside-rules.jsonl"
    link_target.write_bytes(b"outside\n")
    try:
        output_path.symlink_to(link_target)
    except OSError as error:
        pytest.skip(f"symbolic links are unavailable: {error}")

    with pytest.raises(ValueError, match="symbolic-link output path"):
        build_module.build_dataset(
            source_path=Path("unused-source.json"),
            stats_path=Path("unused-stats.json"),
            data_root=data_root,
            verified_on=date(2026, 9, 4),
            upstream_revision=EXPECTED_UPSTREAM_REVISION,
        )

    assert link_target.read_bytes() == b"outside\n"


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are Windows-specific")
def test_build_refuses_directory_junction_escape(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = data_root / "canonical"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        pytest.skip(f"directory junctions are unavailable: {completed.stderr}")

    try:
        with pytest.raises(ValueError, match="escapes data_root"):
            build_module.build_dataset(
                source_path=Path("unused-source.json"),
                stats_path=Path("unused-stats.json"),
                data_root=data_root,
                verified_on=date(2026, 9, 4),
                upstream_revision=EXPECTED_UPSTREAM_REVISION,
            )
    finally:
        junction.rmdir()
