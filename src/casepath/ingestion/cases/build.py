"""Deterministic P3 canonical case build, staged validation, and atomic publish."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from casepath.contracts import ProvisionRecord, RuleRecord
from casepath.ingestion.laws.jsonl import read_jsonl, sha256_file, write_json, write_jsonl
from casepath.ingestion.laws.manifest import ManifestFile

from .cleaner import CaseCleaner
from .ids import (
    DATASET_ID,
    EXPECTED_PROCESSED_CASES_PATH,
    EXPECTED_PROCESSED_CASES_SHA256,
    EXPECTED_UPSTREAM_RECORD_COUNT,
    EXPECTED_UPSTREAM_REPOSITORY_URL,
    EXPECTED_UPSTREAM_REVISION,
    GENERATOR,
    REVIEWED_L2_CASE_IDS,
)
from .manifest import CaseDatasetManifest
from .validation import (
    CaseDatasetValidationReport,
    validate_canonical_cases,
    validate_records,
)

PINNED_GENERATED_ON = date(2026, 9, 6)
GENERATED_PATHS = (
    Path("canonical/cases/cases_l1.jsonl"),
    Path("canonical/cases/cases_deep.jsonl"),
    Path("manifests/cases.manifest.json"),
)
LIMITATIONS = [
    "Machine-extracted L1 cases are not human-verified; L2 requires REVIEWED_L2_CASE_IDS.",
    (
        "Citations that are not Civil Code articles 1-1260 are not written to "
        "related_provision_ids and are not retained as raw citations in this dataset."
    ),
    (
        "Only cases whose pleadings clearly seek service-contract termination and unperformed-fee "
        "refund enter the deep comparison set. Property-damage claims are excluded from that template."
    ),
    "CourtFinding records are taken from 法院经审理查明/法院认定, never from 诉称 or 辩称.",
    "Condition mapping is conservative machine heuristics and always sets human_verified=false.",
]


@dataclass(frozen=True)
class BuildResult:
    manifest_path: Path
    validation: CaseDatasetValidationReport


def _missing_directory_chain(data_root: Path) -> list[Path]:
    missing: list[Path] = []
    for relative_path in GENERATED_PATHS:
        candidate = (data_root / relative_path).parent
        while candidate != data_root.parent and not candidate.exists():
            if candidate not in missing:
                missing.append(candidate)
            candidate = candidate.parent
    return sorted(missing, key=lambda path: len(path.parts))


def _publish_staged_outputs(staged_data_root: Path, data_root: Path) -> None:
    """Replace only P3 outputs and restore every replaced file on failure."""

    backup_container = Path(
        tempfile.mkdtemp(prefix=f".{data_root.name}.backup-", dir=data_root.parent)
    )
    backup_data_root = backup_container / data_root.name
    existing_paths = [path for path in GENERATED_PATHS if (data_root / path).is_file()]
    created_directories = _missing_directory_chain(data_root)
    published_paths: list[Path] = []

    try:
        for relative_path in existing_paths:
            backup_path = backup_data_root / relative_path
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(data_root / relative_path, backup_path)

        for directory in created_directories:
            directory.mkdir(exist_ok=True)

        # Manifest is last so consumers never see new hashes before both JSONL files exist.
        for relative_path in GENERATED_PATHS:
            staged_path = staged_data_root / relative_path
            target_path = data_root / relative_path
            staged_path.replace(target_path)
            published_paths.append(relative_path)
    except Exception:
        rollback_errors: list[OSError] = []
        for relative_path in reversed(published_paths):
            target_path = data_root / relative_path
            backup_path = backup_data_root / relative_path
            try:
                if backup_path.is_file():
                    backup_path.replace(target_path)
                else:
                    target_path.unlink(missing_ok=True)
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except OSError:
                pass
        if rollback_errors:
            raise RuntimeError(
                "P3 publication rollback failed; unrecovered backups remain at "
                f"{backup_data_root}"
            ) from None
        shutil.rmtree(backup_container, ignore_errors=True)
        raise
    else:
        shutil.rmtree(backup_container, ignore_errors=True)


def _stage_rules(final_data_root: Path, staged_data_root: Path) -> None:
    source_rules = final_data_root / "canonical" / "rules"
    target_rules = staged_data_root / "canonical" / "rules"
    if not (source_rules / "provisions.jsonl").is_file() or not (
        source_rules / "rules.jsonl"
    ).is_file():
        raise ValueError("P2 provisions and rules are required to validate P3")
    shutil.copytree(source_rules, target_rules)


def build_dataset(
    input_path: Path,
    data_root: Path,
    *,
    generated_on: date = PINNED_GENERATED_ON,
    upstream_revision: str | None = None,
    markdown_dir: Path | None = None,
) -> BuildResult:
    revision = upstream_revision or EXPECTED_UPSTREAM_REVISION
    if revision != EXPECTED_UPSTREAM_REVISION:
        raise ValueError(f"unexpected upstream revision: {revision}")
    digest = sha256_file(input_path)
    if digest != EXPECTED_PROCESSED_CASES_SHA256:
        raise ValueError(
            "unexpected processed_cases hash: "
            f"expected {EXPECTED_PROCESSED_CASES_SHA256}, received {digest}"
        )
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) != EXPECTED_UPSTREAM_RECORD_COUNT:
        raise ValueError("processed_cases record count does not match the pinned upstream file")

    final_data_root = data_root
    provisions = read_jsonl(
        final_data_root / "canonical" / "rules" / "provisions.jsonl",
        ProvisionRecord,
    )
    rules = read_jsonl(final_data_root / "canonical" / "rules" / "rules.jsonl", RuleRecord)

    cleaner = CaseCleaner(input_path, markdown_dir)
    lightweight, deep = cleaner.build_records()
    validate_records(lightweight, deep, provisions, rules)

    final_data_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{final_data_root.name}.staging-",
        dir=final_data_root.parent,
    ) as staging_directory:
        staged_data_root = Path(staging_directory) / final_data_root.name
        staged_data_root.mkdir()
        _stage_rules(final_data_root, staged_data_root)

        cases_root = staged_data_root / "canonical" / "cases"
        l1_path = cases_root / "cases_l1.jsonl"
        deep_path = cases_root / "cases_deep.jsonl"
        write_jsonl(l1_path, lightweight)
        write_jsonl(deep_path, deep)

        manifest_root = staged_data_root.parent
        manifest = CaseDatasetManifest(
            dataset_id=DATASET_ID,
            generated_on=generated_on,
            generator=GENERATOR,
            upstream_repository_url=EXPECTED_UPSTREAM_REPOSITORY_URL,
            upstream_revision=revision,
            inputs=[
                ManifestFile(
                    path=EXPECTED_PROCESSED_CASES_PATH,
                    sha256=digest,
                    record_count=len(payload),
                    repository_url=EXPECTED_UPSTREAM_REPOSITORY_URL,
                    revision=revision,
                )
            ],
            outputs=[
                ManifestFile(
                    path=l1_path.relative_to(manifest_root).as_posix(),
                    sha256=sha256_file(l1_path),
                    record_count=len(lightweight),
                ),
                ManifestFile(
                    path=deep_path.relative_to(manifest_root).as_posix(),
                    sha256=sha256_file(deep_path),
                    record_count=len(deep),
                ),
            ],
            l1_case_count=len(lightweight),
            deep_case_count=len(deep),
            contract_versions={"CaseIndexRecord": "1.3", "CaseRecord": "1.1"},
            reviewed_l2_case_ids=sorted(REVIEWED_L2_CASE_IDS),
            review_date=None,
            reviews=[],
            limitations=LIMITATIONS,
        )
        write_json(staged_data_root / "manifests" / "cases.manifest.json", manifest)
        validation = validate_canonical_cases(staged_data_root)
        _publish_staged_outputs(staged_data_root, final_data_root)

    return BuildResult(
        manifest_path=final_data_root / "manifests" / "cases.manifest.json",
        validation=validation,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and validate the CasePath P3 case dataset")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(EXPECTED_PROCESSED_CASES_PATH),
    )
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--generated-on", type=date.fromisoformat, default=PINNED_GENERATED_ON)
    parser.add_argument("--upstream-revision")
    parser.add_argument("--markdown-dir", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    if args.validate_only:
        report = validate_canonical_cases(args.data_root)
        print(report.model_dump_json(indent=2))
        return
    result = build_dataset(
        input_path=args.input,
        data_root=args.data_root,
        generated_on=args.generated_on,
        upstream_revision=args.upstream_revision,
        markdown_dir=args.markdown_dir,
    )
    print(result.validation.model_dump_json(indent=2))
    print(result.manifest_path)


if __name__ == "__main__":
    main()
