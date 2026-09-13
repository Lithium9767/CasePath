"""Offline, annotation-isolated evaluation of the pinned LexRel fact corpus.

Both protocols use weak relation labels, not expert similar-case relevance.
Only facts_text enters the existing BM25 implementation. This module neither
changes the online retrieval workflow nor downloads data or calls a model API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from casepath.adapters.retrieval.retrieval_catalog import BM25Index
from casepath.contracts import CaseIndexRecord

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CORPUS = REPOSITORY_ROOT / "data/derived/lexrel_retrieval"
TRUSTED_RECEIPT = REPOSITORY_ROOT / "data/reports/lexrel_retrieval.manifest.json"
GENERATOR = "casepath.ingestion.cases.retrieval_evaluation:v1"
CORPUS_FILES = frozenset({"cases_l1.jsonl", "labels.jsonl", "qrels.jsonl"})

# Preserve P3's 13 fixed, natural-language topic queries, but obtain judgments
# from the separate labels file and never index annotation-enriched metadata.
QUERY_SPECS = (
    ("loan", "借款交付后借款人未按期还本付息", "借款合同关系"),
    ("guarantee", "保证人是否应对债务承担保证责任", "保证合同关系"),
    ("marriage", "夫妻婚姻关系及共同财产债务争议", "夫妻关系"),
    ("traffic", "机动车交通事故造成损害后的侵权赔偿责任", "机动车交通事故侵权责任法律关系"),
    ("property_insurance", "保险事故发生后财产保险合同理赔争议", "财产保险合同关系"),
    ("sale", "买卖合同交货付款及违约责任争议", "除房屋外的买卖合同关系"),
    ("mortgage_contract", "抵押合同设立及抵押担保责任争议", "抵押合同关系"),
    ("house_sale", "房屋买卖合同履行过户付款争议", "房屋买卖合同关系"),
    ("recourse", "承担清偿责任后向责任人追偿", "追偿权关系"),
    ("labor_service", "提供劳务后报酬支付和损害责任争议", "劳务合同关系"),
    ("lease", "租赁物使用、租金支付及合同解除争议", "租赁合同关系"),
    ("property_service", "物业服务费支付及物业服务义务争议", "物业服务合同关系"),
    ("service", "服务合同履行、解除与费用返还争议", "服务合同关系"),
)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON object key")
        result[key] = value
    return result


def _json(content: bytes | str, context: str) -> Any:
    try:
        return json.loads(content, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError):
        # JSON/Pydantic errors may otherwise echo sensitive fact descriptions.
        raise ValueError(f"invalid JSON in {context}") from None


def _rows(content: bytes, context: str) -> list[dict[str, Any]]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeError:
        raise ValueError(f"invalid UTF-8 in {context}") from None
    result = []
    for number, line in enumerate(lines, 1):
        _require(bool(line.strip()), f"empty row in {context}")
        item = _json(line, f"{context} row {number}")
        _require(isinstance(item, dict), f"non-object row in {context}")
        result.append(item)
    return result


def _strings(value: Any, context: str, *, sorted_unique: bool = True) -> list[str]:
    _require(
        isinstance(value, list) and all(isinstance(item, str) for item in value),
        f"invalid string list in {context}",
    )
    _require(len(value) == len(set(value)), f"duplicate ID or label in {context}")
    if sorted_unique:
        _require(value == sorted(value), f"unsorted list in {context}")
    return value


@dataclass(frozen=True)
class EvaluationCorpus:
    facts: dict[str, str]
    labels: dict[str, frozenset[str]]
    qrels: dict[str, frozenset[str]]
    manifest: dict[str, Any]
    hashes: dict[str, str]
    directory: Path


def load_corpus(directory: Path, *, receipt_path: Path | None = None) -> EvaluationCorpus:
    """Validate the immutable four-file corpus against the committed receipt."""
    directory = directory.resolve()
    receipt_path = TRUSTED_RECEIPT if receipt_path is None else receipt_path
    receipt_bytes = receipt_path.read_bytes()
    manifest_bytes = (directory / "manifest.json").read_bytes()
    receipt = _json(receipt_bytes, "trusted receipt")
    manifest = _json(manifest_bytes, "local manifest")
    _require(
        isinstance(manifest, dict) and manifest == receipt, "manifest differs from trusted receipt"
    )
    specifications = manifest.get("files")
    _require(
        isinstance(specifications, dict) and set(specifications) == CORPUS_FILES,
        "invalid corpus file list",
    )
    contents: dict[str, bytes] = {}
    hashes = {"manifest.json": _digest(manifest_bytes), "trusted_receipt": _digest(receipt_bytes)}
    for name in sorted(CORPUS_FILES):
        specification = specifications[name]
        _require(isinstance(specification, dict), "invalid corpus file specification")
        content = (directory / name).read_bytes()
        _require(len(content) == specification.get("bytes"), f"byte count mismatch for {name}")
        _require(_digest(content) == specification.get("sha256"), f"SHA-256 mismatch for {name}")
        contents[name] = content
        hashes[name] = _digest(content)

    facts: dict[str, str] = {}
    normalized_facts: set[str] = set()
    for number, item in enumerate(_rows(contents["cases_l1.jsonl"], "cases"), 1):
        try:
            case = CaseIndexRecord.model_validate(item)
        except ValueError:
            raise ValueError(f"invalid case schema at row {number}") from None
        _require(case.case_id not in facts, "duplicate case ID")
        _require(case.case_id.startswith("lexrel.") and bool(case.case_id[7:]), "invalid case ID")
        try:
            canonical_id = f"lexrel.{UUID(case.case_id.removeprefix('lexrel.'))}"
        except ValueError:
            raise ValueError("invalid canonical case UUID") from None
        _require(case.case_id == canonical_id, "invalid canonical case UUID")
        _require(bool(case.facts_text.strip()), "empty case facts")
        normalized = re.sub(r"\s+", "", case.facts_text)
        _require(normalized not in normalized_facts, "duplicate normalized case facts")
        # Reject enrichment even though the indexing code below would ignore it.
        _require(
            case.title == f"LexRel {case.case_id.removeprefix('lexrel.')}"
            and case.cause is None
            and not case.keywords
            and not case.summary
            and not case.reasoning_text
            and not case.related_provision_ids
            and case.case_no is None
            and case.court is None
            and case.judgment_date is None
            and case.maturity == "L1",
            "case contains annotation-enriched metadata",
        )
        facts[case.case_id] = case.facts_text
        normalized_facts.add(normalized)
    _require(bool(facts), "empty corpus")

    labels: dict[str, frozenset[str]] = {}
    relation_index: dict[str, set[str]] = defaultdict(set)
    raw_counts: Counter[str] = Counter()
    for item in _rows(contents["labels.jsonl"], "labels"):
        _require(
            set(item) == {"case_id", "relation_types", "raw_relation_types"}, "invalid label schema"
        )
        case_id = item["case_id"]
        _require(isinstance(case_id, str) and case_id in facts, "label references unknown case")
        _require(case_id not in labels, "duplicate label case ID")
        names = _strings(item["relation_types"], "normalized labels")
        raw = _strings(item["raw_relation_types"], "raw labels")
        _require(
            names == sorted({name.strip() for name in raw if name.strip()}),
            "invalid label normalization",
        )
        labels[case_id] = frozenset(names)
        raw_counts.update(raw)
        for name in names:
            relation_index[name].add(case_id)
    _require(set(labels) == set(facts), "labels do not cover the complete corpus")

    qrels: dict[str, frozenset[str]] = {}
    for item in _rows(contents["qrels.jsonl"], "qrels"):
        _require(
            set(item)
            == {
                "query_case_id",
                "relevant_case_ids",
                "exclude_case_ids",
                "evaluable",
                "skip_reason",
            },
            "invalid qrels schema",
        )
        case_id = item["query_case_id"]
        _require(isinstance(case_id, str) and case_id in facts, "qrels references unknown query")
        _require(case_id not in qrels, "duplicate qrels query ID")
        positives = _strings(item["relevant_case_ids"], "qrels positives")
        _require(
            all(positive in facts for positive in positives), "qrels references unknown positive"
        )
        _require(case_id not in positives, "qrels includes its own query as a positive")
        _require(item["exclude_case_ids"] == [case_id], "qrels must exclude its own query")
        expected = set().union(*(relation_index[name] for name in labels[case_id])) - {case_id}
        _require(set(positives) == expected, "qrels positives do not match separate labels")
        _require(item["evaluable"] is bool(expected), "inconsistent qrels evaluable flag")
        skip = (
            None
            if expected
            else "no_shared_relation_peer"
            if labels[case_id]
            else "no_valid_relation_label"
        )
        _require(item["skip_reason"] == skip, "inconsistent qrels skip reason")
        qrels[case_id] = frozenset(expected)
    _require(set(qrels) == set(facts), "qrels do not cover the complete corpus")

    evaluable = sum(bool(positives) for positives in qrels.values())
    unlabeled = sum(not names for names in labels.values())
    expected_counts = {
        "cases": len(facts),
        "unique_case_ids": len(facts),
        "whitespace_normalized_text_duplicates": 0,
        "raw_relation_types": len(raw_counts),
        "normalized_relation_types": len(relation_index),
        "unlabeled_distractors": unlabeled,
        "labeled_without_peer": len(facts) - evaluable - unlabeled,
        "evaluable_queries": evaluable,
        "excluded_no_positive_queries": len(facts) - evaluable,
        "directed_positive_pairs": sum(len(positives) for positives in qrels.values()),
    }
    counts = manifest.get("counts", {})
    _require(isinstance(counts, dict), "invalid manifest counts")
    _require(
        all(counts.get(key) == value for key, value in expected_counts.items()),
        "manifest counts do not match corpus",
    )
    _require(
        manifest.get("raw_relation_document_counts") == dict(raw_counts),
        "raw label counts mismatch",
    )
    _require(
        manifest.get("relation_document_counts")
        == {name: len(ids) for name, ids in relation_index.items()},
        "normalized label counts mismatch",
    )
    return EvaluationCorpus(facts, labels, qrels, manifest, hashes, directory)


@dataclass(frozen=True)
class EvaluationQuery:
    query_id: str
    text: str
    positives: frozenset[str]
    excluded: frozenset[str]
    skip_reason: str | None = None


def build_queries(corpus: EvaluationCorpus, protocol: str) -> list[EvaluationQuery]:
    if protocol == "relation_type":
        queries = [
            EvaluationQuery(
                f"lexrel.{suffix}",
                text,
                frozenset(
                    case_id for case_id, labels in corpus.labels.items() if relation in labels
                ),
                frozenset(),
            )
            for suffix, text, relation in QUERY_SPECS
        ]
        _require(
            all(query.positives for query in queries), "fixed relation query has no positive cases"
        )
        return queries
    _require(protocol == "case_overlap", "unknown evaluation protocol")
    return [
        EvaluationQuery(
            case_id,
            corpus.facts[case_id],
            corpus.qrels[case_id],
            frozenset({case_id}),
            None
            if corpus.qrels[case_id]
            else "no_shared_relation_peer"
            if corpus.labels[case_id]
            else "no_valid_relation_label",
        )
        for case_id in sorted(corpus.facts)
    ]


def _validate_k(values: list[int]) -> list[int]:
    _require(
        bool(values) and all(type(k) is int and k > 0 for k in values),
        "k must contain positive integers",
    )
    _require(len(values) == len(set(values)), "duplicate k values")
    return sorted(values)


def load_predictions(
    path: Path, corpus: EvaluationCorpus, queries: list[EvaluationQuery]
) -> tuple[dict[str, list[str]], str]:
    """Read JSONL {query_id, ranked_case_ids}; require every evaluable query."""
    content = path.read_bytes()
    expected_ids = {query.query_id for query in queries if query.positives}
    predictions: dict[str, list[str]] = {}
    for item in _rows(content, "predictions"):
        _require(set(item) == {"query_id", "ranked_case_ids"}, "invalid prediction schema")
        query_id = item["query_id"]
        _require(
            isinstance(query_id, str) and query_id in expected_ids,
            "unknown or skipped prediction query",
        )
        _require(query_id not in predictions, "duplicate prediction query ID")
        ranked = _strings(item["ranked_case_ids"], "prediction ranking", sorted_unique=False)
        _require(
            all(case_id in corpus.facts for case_id in ranked), "prediction references unknown case"
        )
        predictions[query_id] = ranked
    _require(set(predictions) == expected_ids, "predictions must cover every evaluable query")
    return predictions, _digest(content)


def evaluate(
    corpus: EvaluationCorpus,
    *,
    protocol: str = "relation_type",
    k: list[int] | None = None,
    predictions_path: Path | None = None,
) -> dict[str, Any]:
    cutoffs = _validate_k([5, 10, 20] if k is None else k)
    queries = build_queries(corpus, protocol)
    predictions = None
    prediction_hash = None
    if predictions_path is not None:
        predictions, prediction_hash = load_predictions(predictions_path, corpus, queries)
    # This is the sole retrieval input: no title, cause, relation, keyword,
    # summary, inferred claim, outcome, or labels/qrels are sent to the index.
    index = BM25Index(dict(corpus.facts)) if predictions is None else None
    results = []
    totals = {f"macro_recall@{cutoff}": 0.0 for cutoff in cutoffs}
    evaluated = 0
    for query in queries:
        row: dict[str, Any] = {
            "query_id": query.query_id,
            "evaluable": bool(query.positives),
            "skip_reason": query.skip_reason,
            "positive_count": len(query.positives),
            "excluded_candidate_count": len(query.excluded),
            "returned_counts": {},
            "recall": {},
        }
        if query.positives:
            evaluated += 1
            if predictions is not None:
                ranked = predictions[query.query_id]
            else:
                # Over-fetch every excluded ID so removing self cannot shrink
                # an otherwise available top-k result. Filter before slicing.
                ranked = [
                    case_id
                    for case_id, _ in index.search(query.text, max(cutoffs) + len(query.excluded))
                ]
            ranked = [case_id for case_id in ranked if case_id not in query.excluded]
            for cutoff in cutoffs:
                top = ranked[:cutoff]
                recall = len(set(top) & query.positives) / len(query.positives)
                row["returned_counts"][str(cutoff)] = len(top)
                row["recall"][f"recall@{cutoff}"] = recall
                totals[f"macro_recall@{cutoff}"] += recall
        results.append(row)
    _require(evaluated > 0, "no evaluable queries")
    return {
        "generator": GENERATOR,
        "dataset_id": corpus.manifest["dataset_id"],
        "protocol": protocol,
        "retriever": "existing_bm25_default_k1_1.2_b_0.75"
        if predictions is None
        else "external_rankings_not_audited",
        "source": corpus.manifest["source"],
        "data_sha256": corpus.hashes,
        "predictions_sha256": prediction_hash,
        "k": cutoffs,
        "counts": {
            "candidates": len(corpus.facts),
            "queries": len(queries),
            "evaluated": evaluated,
            "skipped_no_positive": len(queries) - evaluated,
        },
        "metrics": {name: total / evaluated for name, total in totals.items()},
        "policy": {
            "candidate_text": "facts_text only; annotation fields excluded",
            "label_access": "separate labels/qrels are used only to establish evaluation judgments",
            "self_match": "case_overlap excludes query case before top-k slicing; relation_type has no query case",
            "denominator": "all labeled positives per query; macro average only over evaluable queries",
            "missing_predictions": "reject incomplete query coverage; empty ranking counts as zero recall",
            "external_rankings": "provenance and annotation isolation are not established by this scorer"
            if predictions is not None
            else None,
        },
        "limitations": [
            "Weak relation-overlap/topic labels are not expert similar-case or outcome relevance judgments.",
            "P3's original 13-query score used a smaller candidate pool and annotation-enriched metadata; scores are not directly comparable.",
            "Relation types are imbalanced; small k and many positives impose a low achievable recall ceiling.",
            "The test split is evaluation-only; tuning on these labels invalidates an unbiased test claim.",
            "Unlabeled candidates are not human-verified negatives; exact deduplication does not establish semantic independence.",
            "This report tests an offline baseline, not the deployed P4 or vector retrieval pipeline.",
        ],
        "queries": results,
    }


def write_report(
    report: dict[str, Any], output: Path, corpus: Path, predictions: Path | None = None
) -> None:
    output = output.resolve()
    protected = [REPOSITORY_ROOT / "data" / name for name in ("canonical", "inputs", "manifests")]
    protected.append(corpus.resolve())
    for path in protected:
        resolved = path.resolve()
        _require(
            not output.is_relative_to(resolved) and not resolved.is_relative_to(output),
            "report output overlaps protected data",
        )
    _require(output != TRUSTED_RECEIPT.resolve(), "report output overlaps trusted receipt")
    _require(
        predictions is None or output != predictions.resolve(), "report output overlaps predictions"
    )
    _require(output.suffix.lower() == ".json", "report output must be a JSON file")
    content = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if output.exists():
        _require(
            output.is_file() and output.read_bytes() == content,
            "refusing to overwrite an existing different report",
        )
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument(
        "--protocol", choices=("relation_type", "case_overlap"), default="relation_type"
    )
    parser.add_argument("--k", type=int, nargs="+", default=[5, 10, 20])
    parser.add_argument(
        "--predictions",
        type=Path,
        help="Optional JSONL {query_id, ranked_case_ids} for every evaluable query",
    )
    parser.add_argument(
        "--output", type=Path, help="Write a new aggregate/per-query report without any fact texts"
    )
    args = parser.parse_args(argv)
    try:
        _validate_k(args.k)
        corpus = load_corpus(args.corpus)
        report = evaluate(
            corpus, protocol=args.protocol, k=args.k, predictions_path=args.predictions
        )
        if args.output is not None:
            write_report(report, args.output, args.corpus, args.predictions)
    except (OSError, TypeError, ValueError):
        # Never print exception inputs, file contents, or sensitive case text.
        parser.exit(
            2,
            "Retrieval evaluation failed: invalid/untrusted data, arguments, predictions, or output; no result published.\n",
        )
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "queries"},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
