"""Build a local, fact-input-only LexRel corpus and relation-overlap qrels.

This auxiliary corpus never enters the canonical 54-case build. LexRel's
original test split remains evaluation data: using its labels for training or
tuning invalidates an unbiased test claim. The qrels below are weak supervision
for relation retrieval, not expert judgments of similar cases or court outcomes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from casepath.contracts import CaseIndexRecord, MaturityLevel
from casepath.ingestion.laws.jsonl import render_jsonl

from .ids import EXPECTED_LEXREL_REPOSITORY_URL, EXPECTED_LEXREL_REVISION

GENERATOR = "casepath.ingestion.cases.lexrel_retrieval:v1"
EXPECTED_TEST_SHA256 = "4dbc837e1f287fe2bc322523494061a70da2b49c344250e2bbe40065aea334a0"
EXPECTED_TEST_BYTES = 2_100_237
EXPECTED_TEST_COUNT = 1_140
TEST_PATH = "LexRel/test.json"
TEST_URL = f"https://raw.githubusercontent.com/thunlp/LexRel/{EXPECTED_LEXREL_REVISION}/{TEST_PATH}"
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "data/derived/lexrel_retrieval"
EXISTING_SERVICE_IDS = frozenset(
    {
        "102f10d1-ca09-4ed1-ae2e-f45007ce3038",
        "1ec928ab-bb1e-4131-bafc-e17593454e1b",
        "55d87466-4e50-4e16-bfd3-7ff06aaeaad6",
        "698f1ab4-dae7-47b5-a0b3-3b4c64f954aa",
        "829d6968-3b55-43c8-9e18-9d6ccccfd391",
        "83dfccf1-1ba7-47d5-890a-a97e0208c09e",
        "8a246ddf-5387-4d25-b4fb-cd1847d2c66b",
        "aea77fcf-9dae-4e62-85b6-05c56d79e332",
        "dc2bb512-fe17-4ec2-bc66-00703647dc6c",
    }
)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _official_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname != "raw.githubusercontent.com"
        or parts.port not in (None, 443)
        or parts.username is not None
        or parts.password is not None
    ):
        raise ValueError("LexRel download and redirects must stay on the official HTTPS raw host")


class _OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_snapshot() -> bytes:
    """Fetch only the pinned official URL; nothing is persisted by this function."""
    _official_url(TEST_URL)
    opener = urllib.request.build_opener(_OfficialRedirects())
    request = urllib.request.Request(TEST_URL, headers={"User-Agent": "CasePath-LexRel-audit/1"})
    with opener.open(request, timeout=60) as response:
        _official_url(response.geturl())
        content = response.read(EXPECTED_TEST_BYTES + 1)
    if len(content) > EXPECTED_TEST_BYTES:
        raise ValueError("LexRel download exceeds the pinned snapshot size")
    return content


def validate_snapshot(content: bytes) -> list[dict[str, Any]]:
    """Require the exact upstream bytes, complete schema, and unique IDs/texts."""
    if _sha256(content) != EXPECTED_TEST_SHA256:
        raise ValueError("LexRel SHA-256 mismatch; expected the exact pinned test.json bytes")
    if len(content) != EXPECTED_TEST_BYTES:
        raise ValueError("LexRel byte count does not match the pinned snapshot")
    payload = json.loads(content.decode("utf-8"))
    if not isinstance(payload, list) or len(payload) != EXPECTED_TEST_COUNT:
        raise ValueError(f"LexRel test snapshot must contain {EXPECTED_TEST_COUNT} records")
    ids: set[str] = set()
    normalized_texts: set[str] = set()
    for row_number, sample in enumerate(payload, 1):
        if not isinstance(sample, dict) or set(sample) != {"uniqid", "input", "relation"}:
            raise ValueError(f"LexRel row {row_number} has an unexpected schema")
        uniqid = sample["uniqid"]
        if not isinstance(uniqid, str) or str(UUID(uniqid)) != uniqid:
            raise ValueError(f"LexRel row {row_number} requires a canonical UUID")
        if uniqid in ids:
            raise ValueError(f"Duplicate LexRel UUID: {uniqid}")
        ids.add(uniqid)
        facts = sample["input"]
        if not isinstance(facts, str) or not facts.strip():
            raise ValueError(f"LexRel row {row_number} has empty or non-string input")
        normalized = re.sub(r"\s+", "", facts)
        if normalized in normalized_texts:
            raise ValueError(f"Duplicate whitespace-normalized LexRel input at row {row_number}")
        normalized_texts.add(normalized)
        relations = sample["relation"]
        if not isinstance(relations, dict):
            raise TypeError(f"LexRel row {row_number} relation must be an object")
        for name, arguments in relations.items():
            if not isinstance(name, str) or not isinstance(arguments, list) or not arguments:
                raise ValueError(f"LexRel row {row_number} has malformed relation arguments")
            for argument in arguments:
                if not isinstance(argument, dict) or set(argument) != {"主体", "客体", "内容"}:
                    raise ValueError(f"LexRel row {row_number} has malformed argument fields")
                if any(
                    value is not None and not isinstance(value, str) for value in argument.values()
                ):
                    raise ValueError(
                        f"LexRel row {row_number} argument values must be strings/null"
                    )
    return sorted(payload, key=lambda sample: sample["uniqid"])


@dataclass(frozen=True)
class RetrievalBuild:
    cases: list[CaseIndexRecord]
    labels: list[dict[str, Any]]
    qrels: list[dict[str, Any]]
    manifest: dict[str, Any]

    def files(self) -> dict[str, bytes]:
        files = {
            "cases_l1.jsonl": render_jsonl(self.cases).encode("utf-8"),
            "labels.jsonl": render_jsonl(self.labels).encode("utf-8"),
            "qrels.jsonl": render_jsonl(self.qrels).encode("utf-8"),
        }
        manifest = dict(self.manifest)
        manifest["files"] = {
            name: {"sha256": _sha256(content), "bytes": len(content)}
            for name, content in files.items()
        }
        files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        return files


def build_snapshot(content: bytes) -> RetrievalBuild:
    samples = validate_snapshot(content)
    cases: list[CaseIndexRecord] = []
    labels: list[dict[str, Any]] = []
    relation_index: dict[str, set[str]] = defaultdict(set)
    raw_counts: Counter[str] = Counter()
    for sample in samples:
        case_id = f"lexrel.{sample['uniqid']}"
        # Relation names, subjects, objects and rights/obligations are evaluation
        # annotations. None may enrich the title, summary, keywords or facts.
        cases.append(
            CaseIndexRecord(
                case_id=case_id,
                title=f"LexRel {sample['uniqid']}",
                facts_text=sample["input"],
                maturity=MaturityLevel.L1,
            )
        )
        raw_names = sorted(sample["relation"])
        names = sorted({name.strip() for name in raw_names if name.strip()})
        raw_counts.update(raw_names)
        labels.append(
            {"case_id": case_id, "relation_types": names, "raw_relation_types": raw_names}
        )
        for name in names:
            relation_index[name].add(case_id)

    qrels: list[dict[str, Any]] = []
    for label in labels:
        peers: set[str] = set()
        for name in label["relation_types"]:
            peers.update(relation_index[name])
        peers.discard(label["case_id"])
        qrels.append(
            {
                "query_case_id": label["case_id"],
                "relevant_case_ids": sorted(peers),
                "exclude_case_ids": [label["case_id"]],
                "evaluable": bool(peers),
                "skip_reason": (
                    None
                    if peers
                    else "no_shared_relation_peer"
                    if label["relation_types"]
                    else "no_valid_relation_label"
                ),
            }
        )
    actual_ids = {sample["uniqid"] for sample in samples}
    overlap_ids = sorted(actual_ids & EXISTING_SERVICE_IDS)
    unlabeled = sum(not label["relation_types"] for label in labels)
    evaluable = sum(row["evaluable"] for row in qrels)
    manifest = {
        "generator": GENERATOR,
        "dataset_id": "dataset.lexrel_retrieval.test.v1",
        "source": {
            "repository_url": EXPECTED_LEXREL_REPOSITORY_URL,
            "revision": EXPECTED_LEXREL_REVISION,
            "path": TEST_PATH,
            "download_url": TEST_URL,
            "sha256": _sha256(content),
            "bytes": len(content),
            "split": "test",
        },
        "counts": {
            "cases": len(cases),
            "existing_canonical_overlap": len(overlap_ids),
            "additional_to_existing_cases": len(cases) - len(overlap_ids),
            "unique_case_ids": len(actual_ids),
            "whitespace_normalized_text_duplicates": 0,
            "raw_relation_types": len(raw_counts),
            "normalized_relation_types": len(relation_index),
            "empty_relation_objects": sum(not sample["relation"] for sample in samples),
            "unlabeled_distractors": unlabeled,
            "labeled_without_peer": len(cases) - evaluable - unlabeled,
            "evaluable_queries": evaluable,
            "excluded_no_positive_queries": len(cases) - evaluable,
            "directed_positive_pairs": sum(len(row["relevant_case_ids"]) for row in qrels),
        },
        "existing_canonical_overlap_ids": [f"lexrel.{uniqid}" for uniqid in overlap_ids],
        "relation_label_normalization": "Strip outer whitespace; discard blank labels; no semantic aliases.",
        "raw_relation_document_counts": dict(sorted(raw_counts.items())),
        "relation_document_counts": {
            name: len(ids) for name, ids in sorted(relation_index.items())
        },
        "evaluation": {
            "task": "leave_one_case_out_relation_overlap_retrieval",
            "supervision": "weak_relation_overlap_not_similar_case_or_outcome_gold",
            "query_text": "cases_l1.jsonl facts_text selected by query_case_id",
            "candidate_text": "facts_text only",
            "relevance": "1 iff another case shares at least one normalized relation type",
            "self_match_policy": "Remove exclude_case_ids before ranking and computing recall@k.",
            "recall_denominator": "Only evaluable queries; per query use all relevant_case_ids.",
            "zero_positive_policy": "Skip 49 no-peer queries in the pinned snapshot, including 33 unlabeled distractors.",
            "labels_access": "Keep labels/qrels separate from retrieval indexes and query construction.",
            "split_policy": "Original LexRel test split: evaluation only. Do not train/tune on its labels and claim unbiased test performance.",
        },
        "limitations": [
            "Only upstream input is indexed; it is an extracted fact description, not verified full judgment text.",
            "Input can mention prior judgments, party requests, medical details and personal identifiers.",
            "No court, case number, judgment date, cause, claims, reasoning or decisions are inferred.",
            "Subject ordering and relation rights/obligations do not establish plaintiff/defendant roles or requested remedies.",
            "Raw relation labels include spelling and wording variants; only whitespace is normalized.",
            "Exact/whitespace-normalized deduplication does not establish semantic independence.",
            "Broad and imbalanced relation overlap is not an expert similar-case relevance judgment.",
            "The existing 9 LexRel cases overlap this corpus; deduplicate by case_id when combining corpora.",
            "The canonical 54-case dataset is not modified by this auxiliary build.",
        ],
        "publication": {
            "upstream_license": "No explicit repository/data license found at the pinned revision.",
            "redistribution_authorization": "not_established",
            "handling": "Local research artifact; publish code, source URLs, hashes and aggregate metadata only.",
            "privacy": "Source inputs can contain personal/sensitive information; public availability is not an anonymization guarantee.",
        },
    }
    return RetrievalBuild(cases, labels, qrels, manifest)


def validate_output_path(output: Path, input_path: Path | None = None) -> Path:
    output = output.resolve()
    protected = [REPOSITORY_ROOT / "data/canonical", REPOSITORY_ROOT / "data/inputs"]
    for path in protected:
        resolved = path.resolve()
        if output.is_relative_to(resolved) or resolved.is_relative_to(output):
            raise ValueError("Output must not overlap canonical data or source inputs")
    if input_path is not None:
        source = input_path.resolve()
        if source.is_relative_to(output) or output.is_relative_to(source):
            raise ValueError("Output must not overlap the input path")
    return output


def write_build(build: RetrievalBuild, output: Path, input_path: Path | None = None) -> Path:
    output = validate_output_path(output, input_path)
    files = build.files()
    if output.exists():
        if not output.is_dir() or {path.name for path in output.iterdir()} != set(files):
            raise ValueError(
                "Refusing existing output that is not this generator's complete dataset"
            )
        if any(
            (output / name).is_symlink()
            or not (output / name).is_file()
            or (output / name).read_bytes() != content
            for name, content in files.items()
        ):
            raise ValueError(
                "Refusing to overwrite changed or foreign output; choose a new directory"
            )
        return output / "manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".lexrel-retrieval-", dir=output.parent) as staging:
        staged = Path(staging) / "dataset"
        staged.mkdir()
        for name, content in files.items():
            (staged / name).write_bytes(content)
        if output.exists():
            raise ValueError("Output appeared during build; refusing to overwrite it")
        staged.rename(output)
    return output / "manifest.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--download", action="store_true", help="Explicitly fetch the pinned official snapshot"
    )
    source.add_argument("--input", type=Path, help="Read exact pinned test.json bytes locally")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check-only", action="store_true", help="Validate and report in memory; write nothing"
    )
    args = parser.parse_args(argv)
    try:
        output = validate_output_path(args.output, args.input)
        content = download_snapshot() if args.download else args.input.read_bytes()
        build = build_snapshot(content)
        if not args.check_only:
            write_build(build, output, args.input)
    except (OSError, TypeError, ValueError, urllib.error.URLError) as error:
        parser.exit(2, f"LexRel retrieval build failed: {error}\n")
    print(build.files()["manifest.json"].decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
