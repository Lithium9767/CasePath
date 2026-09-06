"""Deterministic P3 canonical case build and validation."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from casepath.ingestion.laws.jsonl import sha256_file, write_json, write_jsonl
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
from .validation import CaseDatasetValidationReport, validate_canonical_cases

PINNED_GENERATED_ON = date(2026, 9, 6)
LIMITATIONS = [
    "Machine-extracted L1 cases are not human-verified; L2 requires REVIEWED_L2_CASE_IDS.",
    (
        "related_provision_ids contain only Civil Code articles 1-1260. Judicial interpretations "
        "and other statutes remain unresolved raw citations and are not rewritten as civil-code IDs."
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

    cleaner = CaseCleaner(input_path, markdown_dir)
    lightweight, deep = cleaner.build_records()
    cases_root = data_root / "canonical" / "cases"
    l1_path = cases_root / "cases_l1.jsonl"
    deep_path = cases_root / "cases_deep.jsonl"
    write_jsonl(l1_path, lightweight)
    write_jsonl(deep_path, deep)

    manifest_root = data_root.parent
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
    manifest_path = data_root / "manifests" / "cases.manifest.json"
    write_json(manifest_path, manifest)
    validation = validate_canonical_cases(data_root)
    return BuildResult(manifest_path=manifest_path, validation=validation)


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
