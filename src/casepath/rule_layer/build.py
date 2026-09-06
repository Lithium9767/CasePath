from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from casepath.ingestion.laws.civil_code import (
    CIVIL_CODE_SOURCE_ID,
    EXPECTED_SOURCE_SHA256,
    EXPECTED_STATS_SHA256,
    EXPECTED_UPSTREAM_REVISION,
    convert_civil_code,
    load_civil_code,
)
from casepath.ingestion.laws.jsonl import sha256_file, write_json, write_jsonl
from casepath.ingestion.laws.manifest import (
    CivilCodeManifest,
    ManifestFile,
    TransformationRecord,
)
from casepath.rule_layer.civil_code import build_civil_code_rules
from casepath.rule_layer.ids import REVIEWED_L3_RULE_IDS
from casepath.rule_layer.source_review import authority_verification_snapshot
from casepath.rule_layer.validation import DatasetValidationReport, validate_canonical_dataset

UPSTREAM_REPOSITORY_URL = "https://github.com/litunan/legal-rag"
SPECIALISED_PREPAID_SERVICE_RULES_URL = "https://www.court.gov.cn/zixun/xiangqing/459321.html"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
GENERATED_PATHS = (
    Path("canonical/rules/legal_sources.jsonl"),
    Path("canonical/rules/provisions.jsonl"),
    Path("canonical/rules/rules.jsonl"),
    Path("canonical/rules/source_spans.jsonl"),
    Path("manifests/civil_code.manifest.json"),
)


@dataclass(frozen=True)
class BuildResult:
    manifest_path: Path
    validation: DatasetValidationReport


def _git_revision(source_path: Path) -> str:
    repository_root = source_path.resolve().parents[2]
    completed = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    revision = completed.stdout.strip()
    if not revision:
        raise ValueError(f"Git returned an empty revision for {repository_root}")
    return revision


def _validate_stats(stats_path: Path) -> int:
    stats_sha256 = sha256_file(stats_path)
    if stats_sha256 != EXPECTED_STATS_SHA256:
        raise ValueError(
            "unexpected civil-code statistics hash: "
            f"expected {EXPECTED_STATS_SHA256}, received {stats_sha256}"
        )
    payload = json.loads(stats_path.read_text(encoding="utf-8"))
    total_articles = payload.get("total_articles")
    if total_articles != 1260:
        raise ValueError(f"statistics file declares {total_articles!r} articles, expected 1260")
    return total_articles


def _manifest_path(path: Path, data_root: Path) -> str:
    return path.relative_to(data_root.parent).as_posix()


def _missing_directory_chain(data_root: Path) -> list[Path]:
    missing: list[Path] = []
    for relative_path in GENERATED_PATHS:
        candidate = (data_root / relative_path).parent
        while candidate != data_root.parent and not candidate.exists():
            if candidate not in missing:
                missing.append(candidate)
            candidate = candidate.parent
    return sorted(missing, key=lambda path: len(path.parts))


def _validate_generated_output_paths(data_root: Path) -> None:
    if data_root.exists() and data_root.resolve() != data_root.absolute():
        raise ValueError(f"refusing redirected data_root: {data_root}")
    resolved_data_root = data_root.resolve(strict=False)
    for relative_path in GENERATED_PATHS:
        target_path = data_root / relative_path
        candidate = target_path
        while candidate != data_root.parent:
            if candidate.is_symlink():
                raise ValueError(f"refusing symbolic-link output path: {candidate}")
            candidate = candidate.parent
        resolved_target_path = target_path.resolve(strict=False)
        if not resolved_target_path.is_relative_to(resolved_data_root):
            raise ValueError(f"generated output escapes data_root: {target_path}")
        if target_path.exists() and not target_path.is_file():
            raise ValueError(f"generated output exists but is not a file: {target_path}")


def _publish_staged_outputs(staged_data_root: Path, data_root: Path) -> None:
    """Publish only P2 outputs and restore every replaced file on failure."""

    # Recheck immediately before publication to catch a parent replaced with a
    # symlink/junction after the build's initial validation.
    _validate_generated_output_paths(data_root)
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

        # GENERATED_PATHS deliberately lists the manifest last. Consumers will
        # never observe a new manifest before all of its payloads are present.
        for relative_path in GENERATED_PATHS:
            staged_path = staged_data_root / relative_path
            target_path = data_root / relative_path
            if target_path.is_symlink():
                raise ValueError(f"refusing to replace symbolic-link output: {target_path}")
            staged_path.replace(target_path)
            published_paths.append(relative_path)
    except Exception as publish_error:
        rollback_errors: list[Exception] = []
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
                # A concurrent writer may have populated a newly created
                # directory; never remove content outside P2's five files.
                pass

        if rollback_errors:
            raise RuntimeError(
                "dataset publication rollback failed; unrecovered backups remain at "
                f"{backup_data_root}"
            ) from ExceptionGroup("dataset publication errors", [publish_error, *rollback_errors])
        shutil.rmtree(backup_container, ignore_errors=True)
        raise
    else:
        shutil.rmtree(backup_container, ignore_errors=True)


def build_dataset(
    *,
    source_path: Path,
    stats_path: Path,
    data_root: Path,
    verified_on: date,
    upstream_revision: str | None = None,
) -> BuildResult:
    """Build the pinned release; verified_on is the legacy generation-date argument.

    The source comparison and rule review retain their fixed date and hashes,
    independently of the date chosen for this reproducible build.
    """

    final_data_root = data_root.absolute()
    resolved_data_root = final_data_root.resolve(strict=False)
    protected_roots = (
        Path(final_data_root.anchor).resolve(strict=False),
        Path.home().resolve(strict=False),
        PROJECT_ROOT,
    )
    if any(
        protected_root == resolved_data_root or protected_root.is_relative_to(resolved_data_root)
        for protected_root in protected_roots
    ):
        raise ValueError(f"refusing to replace protected data_root: {final_data_root}")
    if final_data_root.is_symlink():
        raise ValueError(f"refusing symbolic-link data_root: {final_data_root}")
    if final_data_root.exists() and not final_data_root.is_dir():
        raise ValueError(f"data_root exists but is not a directory: {final_data_root}")
    _validate_generated_output_paths(final_data_root)

    # Resolve fallible source metadata before preparing output. In particular,
    # Git lookup failures must not leave new JSONL files with an old manifest.
    _validate_stats(stats_path)
    source_sha256 = sha256_file(source_path)
    stats_sha256 = sha256_file(stats_path)
    revision = (upstream_revision or _git_revision(source_path)).strip()
    if revision != EXPECTED_UPSTREAM_REVISION:
        raise ValueError(
            "unexpected upstream revision: "
            f"expected {EXPECTED_UPSTREAM_REVISION}, received {revision or '<empty>'}"
        )

    conversion = convert_civil_code(load_civil_code(source_path))
    rule_build = build_civil_code_rules(conversion.provisions)

    span_by_id = {span.span_id: span for span in conversion.source_spans}
    for span in rule_build.source_spans:
        existing = span_by_id.get(span.span_id)
        if existing is not None and existing != span:
            raise ValueError(f"conflicting source span definition: {span.span_id}")
        span_by_id[span.span_id] = span
    all_spans = list(span_by_id.values())

    # Validate all in-memory cross-file references before serialising a staging tree.
    from casepath.rule_layer.validation import validate_records

    validate_records(
        [conversion.legal_source],
        conversion.provisions,
        rule_build.rules,
        all_spans,
    )

    final_data_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{final_data_root.name}.staging-", dir=final_data_root.parent
    ) as staging_directory:
        staged_data_root = Path(staging_directory) / final_data_root.name
        staged_data_root.mkdir()

        canonical_root = staged_data_root / "canonical" / "rules"
        manifest_root = staged_data_root / "manifests"
        legal_sources_path = canonical_root / "legal_sources.jsonl"
        provisions_path = canonical_root / "provisions.jsonl"
        rules_path = canonical_root / "rules.jsonl"
        spans_path = canonical_root / "source_spans.jsonl"
        staged_manifest_path = manifest_root / "civil_code.manifest.json"

        write_jsonl(legal_sources_path, [conversion.legal_source])
        write_jsonl(provisions_path, conversion.provisions)
        write_jsonl(rules_path, rule_build.rules)
        write_jsonl(spans_path, all_spans)

        output_specs = [
            (legal_sources_path, 1),
            (provisions_path, len(conversion.provisions)),
            (rules_path, len(rule_build.rules)),
            (spans_path, len(all_spans)),
        ]
        manifest = CivilCodeManifest(
            dataset_id="dataset.civil_code.rules.v1",
            source_id=CIVIL_CODE_SOURCE_ID,
            generated_on=verified_on,
            generator="casepath.rule_layer.build:v1",
            upstream_repository_url=UPSTREAM_REPOSITORY_URL,
            upstream_revision=revision,
            inputs=[
                ManifestFile(
                    path="../legal-rag/data/laws/民法典_法条.json",
                    sha256=source_sha256,
                    record_count=1260,
                    repository_url=UPSTREAM_REPOSITORY_URL,
                    revision=revision,
                ),
                ManifestFile(
                    path="../legal-rag/data/laws/民法典_统计.json",
                    sha256=stats_sha256,
                    record_count=1,
                    repository_url=UPSTREAM_REPOSITORY_URL,
                    revision=revision,
                ),
            ],
            outputs=[
                ManifestFile(
                    path=_manifest_path(path, staged_data_root),
                    sha256=sha256_file(path),
                    record_count=record_count,
                )
                for path, record_count in output_specs
            ],
            transformations=[
                TransformationRecord(
                    transformation_id="repair.upstream_hierarchy.flush_order.v1",
                    description=(
                        "Correct the pinned upstream converter's one-record-early book, "
                        "sub-book, chapter, and section metadata using an immutable snapshot."
                    ),
                    affected_records=conversion.hierarchy_repair_count,
                    guard_sha256=EXPECTED_SOURCE_SHA256,
                )
            ],
            authority_verification=authority_verification_snapshot(),
            rule_review_status={
                rule.rule_id: (
                    "verified"
                    if rule.rule_id in REVIEWED_L3_RULE_IDS
                    else "reviewed_with_limitations"
                )
                for rule in rule_build.rules
            },
            limitations=[
                (
                    "The upstream repository declares no license; retain source attribution and "
                    "review redistribution policy before publishing a derived corpus."
                ),
                (
                    "The upstream JSON flattens paragraph and item line breaks into spaces. Source "
                    "span offsets address the canonical flattened text, not PDF byte or page "
                    "offsets."
                ),
                (
                    "Article 563 creates a right to terminate rather than automatic termination; "
                    "Article 565 notice/litigation procedure is included, while Article 564 "
                    "exercise periods remain outside these demonstration rules."
                ),
                (
                    "No fixed refund formula is inferred from Articles 509/563/565/566. Prepaid "
                    f"service disputes also require review of the 2025 judicial interpretation: "
                    f"{SPECIALISED_PREPAID_SERVICE_RULES_URL}"
                ),
            ],
        )
        write_json(staged_manifest_path, manifest)
        validation = validate_canonical_dataset(staged_data_root)
        _publish_staged_outputs(staged_data_root, final_data_root)

    return BuildResult(
        manifest_path=final_data_root / "manifests" / "civil_code.manifest.json",
        validation=validation,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and validate the CasePath P2 dataset")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("../legal-rag/data/laws/民法典_法条.json"),
    )
    parser.add_argument(
        "--stats",
        type=Path,
        default=Path("../legal-rag/data/laws/民法典_统计.json"),
    )
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument(
        "--generated-on",
        "--verified-on",
        dest="generated_on",
        type=date.fromisoformat,
        default=datetime.now(tz=UTC).date(),
        help=(
            "Dataset generation date. --verified-on is a legacy alias; neither option changes "
            "the fixed source-comparison and rule-review date."
        ),
    )
    parser.add_argument("--upstream-revision")
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    if args.validate_only:
        report = validate_canonical_dataset(args.data_root)
        print(report.model_dump_json(indent=2))
        return
    result = build_dataset(
        source_path=args.source,
        stats_path=args.stats,
        data_root=args.data_root,
        verified_on=args.generated_on,
        upstream_revision=args.upstream_revision,
    )
    print(result.validation.model_dump_json(indent=2))
    print(result.manifest_path)


if __name__ == "__main__":
    main()
