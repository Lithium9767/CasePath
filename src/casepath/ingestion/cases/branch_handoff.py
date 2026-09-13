"""Pinned P3/P2 data reconciliation; report IDs and counts, never source facts."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from casepath.contracts import CaseIndexRecord
from casepath.ingestion.laws.jsonl import read_jsonl, sha256_file, write_json

REPOSITORY = "https://github.com/Lithium9767/CasePath"
P3_REVISION = "6be749d0ee957b7508ad879e8016726873d113c8"
P3_FILES = {
    "canonical/cases/cases_l1.jsonl": "86013cdc1266f821cf74fb9664fe97d1d6c878f8d75d1af3d94e52e1d90323f3",
    "canonical/cases/additional_cases.jsonl": "86c16fdf7c0c657f7f679b0a6ba5eb1211d7bcc64b5d1b925e66428022932b23",
    "inputs/lexrel_retrieval_cases.json": "3e7ef52e191217816c97b2fbcab075089e165b65b30dcbaef7d05c684aa95d24",
}


def _unique(records: list[CaseIndexRecord]) -> dict[str, CaseIndexRecord]:
    by_id = {record.case_id: record for record in records}
    facts = [re.sub(r"\s+", "", record.facts_text) for record in records]
    if len(by_id) != len(records) or len(set(facts)) != len(facts) or not all(facts):
        raise ValueError("case IDs/facts must be unique and nonempty")
    return by_id


def reconcile(p2, p3, corpus, subset, extra_ids) -> dict:
    """Compare actual corpora; never add a cohort's overlapping count twice."""
    p2_by_id, p3_by_id, full = map(_unique, (p2, p3, corpus))
    subset_ids = [f"lexrel.{row['sample']['uniqid']}" for row in subset]
    if len(set(subset_ids)) != len(subset_ids):
        raise ValueError("duplicate P3 subset ID")
    for row, key in zip(subset, subset_ids, strict=True):
        if key not in full or key not in p3_by_id:
            raise ValueError("P3 subset is missing from a declared corpus")
        if row["sample"]["input"] != full[key].facts_text:
            raise ValueError("P3 subset fact text differs from the pinned full corpus")
    for index in (p2_by_id, p3_by_id):
        for key in index.keys() & full.keys():
            if index[key].facts_text != full[key].facts_text:
                raise ValueError("same case ID has conflicting fact text")
    for key in p2_by_id.keys() & p3_by_id.keys():
        if p2_by_id[key].case_id.startswith("lexrel."):
            continue
        # The P2 cleaner may retain a separate final-disposition section.
        if not p2_by_id[key].facts_text.startswith(p3_by_id[key].facts_text):
            raise ValueError("shared non-LexRel case needs an explicit content reconciliation")
    union_facts: dict[str, str] = {}
    for key, record in {**p3_by_id, **full, **p2_by_id}.items():
        normalized = re.sub(r"\s+", "", record.facts_text)
        if normalized in union_facts and union_facts[normalized] != key:
            raise ValueError("same normalized facts use different case IDs across corpora")
        union_facts[normalized] = key
    extra_ids = set(extra_ids)
    if not extra_ids <= p2_by_id.keys() or extra_ids & p3_by_id.keys():
        raise ValueError("SPC supplement must be loaded in P2 and separate from P3 canonical")
    p2_overlap = p2_by_id.keys() & full.keys()
    p3_overlap = p3_by_id.keys() & full.keys()
    return {
        "counts": {
            "p2_canonical": len(p2),
            "p3_canonical": len(p3),
            "lexrel_full_evaluation": len(corpus),
            "p3_selected_increment": len(subset_ids),
            "p3_selected_already_in_full": len(set(subset_ids) & full.keys()),
            "p2_full_overlap": len(p2_overlap),
            "p3_full_overlap": len(p3_overlap),
            "full_additional_to_p2": len(full) - len(p2_overlap),
            "full_additional_to_p3": len(full) - len(p3_overlap),
            "p3_unloaded_spc_now_in_p2": len(extra_ids),
            "p2_and_full_distinct_cases": len(p2_by_id.keys() | full.keys()),
            "p3_and_full_distinct_cases": len(p3_by_id.keys() | full.keys()),
        },
        "p3_selected_case_ids": sorted(subset_ids),
        "p2_full_overlap_ids": sorted(p2_overlap),
        "p3_full_overlap_ids": sorted(p3_overlap),
        "spc_l1_only_ids": sorted(extra_ids),
        "matched_lexrel_facts": True,
    }


def build_report(data_root: Path, p3_data_root: Path, corpus_root: Path) -> dict:
    from .validation import validate_canonical_cases

    validate_canonical_cases(data_root)
    for relative, digest in P3_FILES.items():
        if sha256_file(p3_data_root / relative) != digest:
            raise ValueError("P3 file does not match the reviewed commit")
    receipt_path = data_root / "reports/lexrel_retrieval.manifest.json"
    receipt = json.loads(receipt_path.read_text("utf-8"))
    if json.loads((corpus_root / "manifest.json").read_text("utf-8")) != receipt:
        raise ValueError("full corpus differs from its published receipt")
    for name, entry in receipt["files"].items():
        if sha256_file(corpus_root / name) != entry["sha256"]:
            raise ValueError("full corpus file hash mismatch")
    extra = [
        json.loads(line)
        for line in (p3_data_root / "canonical/cases/additional_cases.jsonl")
        .read_text("utf-8")
        .splitlines()
        if line.strip()
    ]
    p2 = read_jsonl(data_root / "canonical/cases/cases_l1.jsonl", CaseIndexRecord)
    p3 = read_jsonl(p3_data_root / "canonical/cases/cases_l1.jsonl", CaseIndexRecord)
    full = read_jsonl(corpus_root / "cases_l1.jsonl", CaseIndexRecord)
    subset = json.loads((p3_data_root / "inputs/lexrel_retrieval_cases.json").read_text("utf-8"))
    comparison = reconcile(p2, p3, full, subset, [row["case_id"] for row in extra])
    if len(extra) != 6 or comparison["counts"]["p3_selected_increment"] != 96:
        raise ValueError("unexpected reviewed P3 cohort size")
    return {
        "report_version": "1.0",
        "checked_on": "2026-09-13",
        "status": "passed",
        "p3_repository": REPOSITORY,
        "p3_revision": P3_REVISION,
        "p3_files_sha256": P3_FILES,
        "p2_files_sha256": {
            name: sha256_file(data_root / name)
            for name in (
                "canonical/cases/cases_l1.jsonl",
                "canonical/cases/cases_deep.jsonl",
                "manifests/cases.manifest.json",
                "canonical/rules/rules.jsonl",
            )
        },
        "full_corpus_receipt_sha256": sha256_file(receipt_path),
        **comparison,
        "decisions": [
            "Keep P2 source-accuracy fixes; do not overwrite the cleaner with the P3 version.",
            "P3's 96 LexRel records are already in the 1140 evaluation corpus; do not count them again.",
            "Keep new LexRel facts local; publish reproducible code and metadata only.",
            "Load six corrected SPC editorial summaries as L1-only, not fabricated deep judgments.",
            "Use facts-only indexes with isolated labels; P3 label-enriched scores are not comparable.",
        ],
        "limitations": [
            "Corpus unions are data availability counts, not deployed online case counts.",
            "Exact ID/text reconciliation does not establish semantic independence or complete legal correctness.",
            "LexRel relationship labels are weak relevance supervision, not similar-judgment gold.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--p3-data-root", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, default=Path("data/derived/lexrel_retrieval"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.output:
        target = args.output.resolve()
        forbidden = [args.p3_data_root.resolve(), args.corpus.resolve()]
        forbidden.extend(
            args.data_root.resolve() / name for name in ("canonical", "inputs", "manifests")
        )
        if any(target == path or target.is_relative_to(path) for path in forbidden):
            parser.error("output must not overwrite data or source inputs")
        if target == (args.data_root / "reports/lexrel_retrieval.manifest.json").resolve():
            parser.error("output must not overwrite the trusted full-corpus receipt")
    try:
        report = build_report(args.data_root, args.p3_data_root, args.corpus)
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(
            2, "P3 handoff failed: source identity, schema or corpus consistency mismatch\n"
        )
    if args.output:
        write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
