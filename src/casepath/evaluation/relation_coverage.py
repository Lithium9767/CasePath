"""C1: fixed LexRel relation-type retrieval in its independently labeled universe.

Candidate scoping precedes index/graph construction. This is a relation-label
task, not case similarity, legal applicability or a production mixed-corpus score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import replace
from pathlib import Path
from uuid import UUID

from casepath.adapters.retrieval.retrieval_catalog import (
    RetrievalCatalog,
    load_canonical_catalog,
)
from casepath.contracts import CaseIndexRecord
from casepath.evaluation.ac_retrieval import (
    _CANONICAL,
    EvaluationQuery,
    _benchmark_manifest,
    _git_info,
    _implementation_hashes,
    _json_digest,
    _read_jsonl,
    _sha,
    _unique_object,
    evaluate_queries,
    facts_only_catalog,
    load_queries,
    write_report,
)
from casepath.evaluation.metrics import score_ranking, validate_k
from casepath.ingestion.cases.lexrel import case_id_for
from casepath.ingestion.laws.jsonl import read_jsonl

_SOURCE_PINS = (
    (
        "inputs/lexrel_service_contract.json",
        "2b62543e8c61eb44664a1066a8a32311c026601ee02c7e4963799e1087a3285e",
        9,
        False,
    ),
    (
        "inputs/lexrel_retrieval_cases.json",
        "3e7ef52e191217816c97b2fbcab075089e165b65b30dcbaef7d05c684aa95d24",
        96,
        True,
    ),
)
_TOPIC_PATH = "evaluation/case_retrieval_qrels.jsonl"
_TOPIC_SHA = "d88fd2f3526df9ce021e28fddb4fa4bf54bc59739e58550572bca5eea34b0bae"
_FAMILY = "weak_relation_label_non_exhaustive"
_CONTRACT_RELATIONS = frozenset(
    {
        "借款合同关系",
        "保证合同关系",
        "财产保险合同关系",
        "除房屋外的买卖合同关系",
        "抵押合同关系",
        "房屋买卖合同关系",
        "劳务合同关系",
        "租赁合同关系",
        "物业服务合同关系",
        "服务合同关系",
    }
)


def _lf_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def source_labels(data_root: Path) -> dict[str, frozenset[str]]:
    """Resolve scope from original UUIDs and labels; never infer labels from titles."""
    light = read_jsonl(data_root / "canonical/cases/cases_l1.jsonl", CaseIndexRecord)
    facts = {row.case_id: row.facts_text for row in light}
    if len(light) != len(facts):
        raise ValueError("duplicate canonical case ID")
    labels: dict[str, frozenset[str]] = {}
    for relative, expected_sha, count, wrapped in _SOURCE_PINS:
        path = data_root / relative
        if _lf_sha(path) != expected_sha:
            raise ValueError("LexRel source hash differs from the pinned snapshot")
        rows = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        if not isinstance(rows, list) or len(rows) != count:
            raise ValueError("LexRel source record count differs from its pin")
        for outer in rows:
            if not isinstance(outer, dict):
                raise ValueError("LexRel source record must be an object")  # noqa: TRY004
            sample = outer.get("sample") if wrapped else outer
            if not isinstance(sample, dict) or not {"uniqid", "input", "relation"} <= set(sample):
                raise ValueError("LexRel sample fields are incomplete")
            uid = sample["uniqid"]
            if not isinstance(uid, str):
                raise ValueError("LexRel UUID must be canonical text")  # noqa: TRY004
            try:
                canonical_uuid = str(UUID(uid))
            except (ValueError, AttributeError) as exc:
                raise ValueError("invalid LexRel source UUID") from exc
            if canonical_uuid != uid:
                raise ValueError("LexRel UUID is not in canonical form")
            case_id = case_id_for(uid)
            if case_id in labels:
                raise ValueError("duplicate source UUID across LexRel inputs")
            if case_id not in facts:
                raise ValueError("source UUID has no canonical candidate")
            text, relation = sample["input"], sample["relation"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("source facts must be nonempty text")
            if text.strip() != facts[case_id].strip():
                raise ValueError("canonical facts do not match the pinned source UUID")
            if (
                not isinstance(relation, dict)
                or not relation
                or any(not isinstance(label, str) or not label.strip() for label in relation)
            ):
                raise ValueError("source relation labels are missing or invalid")
            labels[case_id] = frozenset(label.strip() for label in relation)
    if len(labels) != sum(count for _, _, count, _ in _SOURCE_PINS):
        raise ValueError("source-labeled candidate coverage is incomplete")
    return labels


def relation_queries(
    data_root: Path,
    queries: list[EvaluationQuery],
    labels: dict[str, frozenset[str]],
) -> tuple[list[EvaluationQuery], dict[str, str]]:
    """Keep the old 13 questions and qrels exactly; independently recompute their labels."""
    path = data_root / _TOPIC_PATH
    if _lf_sha(path) != _TOPIC_SHA:
        raise ValueError("the original relation topics changed")
    topics = _read_jsonl(path)
    if len(topics) != 13:
        raise ValueError("C1 requires exactly the original 13 relation topics")
    by_id = {query.query_id: query for query in queries}
    if len(by_id) != len(queries):
        raise ValueError("duplicate evaluation query ID")
    selected, relations = [], {}
    for topic in topics:
        if not {"query_id", "query_text", "relation_type"} <= set(topic):
            raise ValueError("relation topic fields are incomplete")
        query_id = "C." + topic["query_id"]
        if query_id in relations or query_id not in by_id:
            raise ValueError("duplicate or missing original relation query")
        relation = topic["relation_type"]
        expected = {key: 1 for key, values in labels.items() if relation in values}
        query = by_id[query_id]
        if (
            query.module != "C"
            or query.query_text != topic["query_text"]
            or query.qrels != expected
            or not expected
            or query.exclude_ids
            or query.judgment_basis.partition(":")[0] != _FAMILY
        ):
            raise ValueError("C1 query or judgments differ from the source relation definition")
        selected.append(query)
        relations[query_id] = relation
    declared = {
        query.query_id for query in queries if query.judgment_basis.partition(":")[0] == _FAMILY
    }
    if declared != set(relations):
        raise ValueError("unexpected or missing relation-labeled query")
    return selected, relations


def scope_catalog(
    catalog: RetrievalCatalog,
    labels: dict[str, frozenset[str]],
) -> RetrievalCatalog:
    """This copy must be made BEFORE constructing either candidate search or navigator."""
    if not labels or not set(labels) <= catalog.cases.keys():
        raise ValueError("relation scope contains empty or unknown case IDs")
    scoped = replace(
        catalog,
        cases={key: catalog.cases[key] for key in sorted(labels)},
        case_records={key: record for key, record in catalog.case_records.items() if key in labels},
        index_version=_json_digest(
            {
                "parent_index_version": catalog.index_version,
                "protocol": "casepath.c1.lexrel_relation_coverage.v1",
                "case_ids": sorted(labels),
            }
        ),
    )
    if scoped.rules is not catalog.rules or scoped.conditions is not catalog.conditions:
        raise ValueError("C1 must not change the A rule/condition universe")
    return scoped


def select_query_scope(
    queries: list[EvaluationQuery],
    relations: dict[str, str],
    *,
    contract_only: bool,
) -> tuple[list[EvaluationQuery], list[dict]]:
    """Select query topics by the source relation taxonomy, never candidate positives."""
    if not contract_only:
        return queries, []
    selected = [query for query in queries if relations[query.query_id] in _CONTRACT_RELATIONS]
    excluded = [
        {"query_id": query.query_id, "relation_type": relations[query.query_id]}
        for query in queries
        if relations[query.query_id] not in _CONTRACT_RELATIONS
    ]
    if len(selected) != 10 or len(excluded) != 3:
        raise ValueError("contract scope must cover the fixed 10 contract and 3 excluded topics")
    return selected, excluded


def add_coverage_metrics(
    result: dict,
    queries: list[EvaluationQuery],
    labels: dict[str, frozenset[str]],
    *,
    k: tuple[int, ...],
) -> None:
    """Append metrics without redefining Recall/nDCG or treating absent qrels as unknown labels."""
    ks = validate_k(k)
    wanted = {query.query_id: query for query in queries}
    rows = result["queries"]
    if len(rows) != len(wanted) or {row["query_id"] for row in rows} != set(wanted):
        raise ValueError("retrieval did not return every C1 query exactly once")
    fields = ("precision", "recall_ceiling", "source_label_coverage", "unjudged_ratio")
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        query = wanted[row["query_id"]]
        if set(row["rankings"]) != {"c.bm25", "c.fused", "c.pool"}:
            raise ValueError("C1 requires all three independently identified C rankings")
        for stage, ranking in row["rankings"].items():
            ids = ranking["ranked_ids"]
            checked = score_ranking(ids, query.qrels, candidate_ids=labels, k=ks)
            if checked != ranking["metrics"]:
                raise ValueError("reported C1 metrics do not match actual ranked candidates")
            positive_count = checked["positive_count"]
            extra = {}
            for cutoff in ks:
                base = checked["at_k"][str(cutoff)]
                top = ids[:cutoff]
                labeled = sum(key in labels for key in top)
                extra[str(cutoff)] = {
                    "precision": base["relevant_retrieved"] / cutoff,
                    "recall_ceiling": min(cutoff, positive_count) / positive_count
                    if positive_count
                    else None,
                    "source_labeled_returned": labeled,
                    "unjudged_returned": len(top) - labeled,
                    "source_label_coverage": labeled / len(top) if top else None,
                    "unjudged_ratio": (len(top) - labeled) / len(top) if top else None,
                }
            ranking["relation_at_k"] = extra
            grouped.setdefault(stage, []).append(extra)
    result["relation_summary"] = {
        stage: {
            "query_count": len(items),
            "macro_at_k": {
                str(cutoff): {
                    field: (math.fsum(values) / len(values) if values else None)
                    for field in fields
                    for values in [
                        [
                            item[str(cutoff)][field]
                            for item in items
                            if item[str(cutoff)][field] is not None
                        ]
                    ]
                }
                for cutoff in ks
            },
            "empty_ranking_queries": sum(
                item[str(ks[0])]["source_label_coverage"] is None for item in items
            ),
        }
        for stage, items in sorted(grouped.items())
    }


def build_report(
    data_root: Path,
    queries_path: Path | None = None,
    *,
    k=(5, 10, 20),
    contract_only: bool = False,
    case_han_unigram_weight: float = 0.25,
    case_feedback: bool = False,
) -> dict:
    ks = validate_k(k)
    data_root = data_root.resolve()
    queries_path = queries_path or data_root / "evaluation/ac_queries.jsonl"
    paths = [*_CANONICAL, *(path for path, _, _, _ in _SOURCE_PINS), _TOPIC_PATH]
    before = {path: _sha(data_root / path) for path in paths}
    query_hash = _sha(queries_path)
    catalog = facts_only_catalog(data_root, load_canonical_catalog(data_root))
    all_queries = load_queries(queries_path, catalog)
    manifest = _benchmark_manifest(queries_path, data_root, len(all_queries))
    if manifest is None:
        raise ValueError("C1 requires the existing pinned query manifest")
    labels = source_labels(data_root)
    queries, relation_by_query = relation_queries(data_root, all_queries, labels)
    queries, excluded_topics = select_query_scope(
        queries,
        relation_by_query,
        contract_only=contract_only,
    )
    scoped = scope_catalog(catalog, labels)
    code_root = Path(__file__).resolve().parents[3]
    implementation = _implementation_hashes(code_root)
    # No topic labels/qrels are passed into catalog or state; only corpus membership
    # is restricted. The existing runner gives query_text to actual A -> B -> C.
    result = evaluate_queries(
        scoped, queries, k=ks, case_han_unigram_weight=case_han_unigram_weight,
        case_feedback=case_feedback,
    )
    add_coverage_metrics(result, queries, labels, k=ks)
    for row in result["queries"]:
        row["relation_type"] = relation_by_query[row["query_id"]]
    if before != {path: _sha(data_root / path) for path in paths}:
        raise ValueError("C1 source/canonical inputs changed during retrieval")
    if query_hash != _sha(queries_path):
        raise ValueError("C1 queries changed during retrieval")
    if manifest != _benchmark_manifest(queries_path, data_root, len(all_queries)):
        raise ValueError("C1 query manifest changed during retrieval")
    if implementation != _implementation_hashes(code_root):
        raise ValueError("C1 implementation changed during retrieval")
    return {
        "schema_version": "1.0",
        "protocol": "casepath.c1.lexrel_relation_coverage.v1",
        "task": "relation_type_retrieval",
        "query_scope": "contract_relation_types" if contract_only else "all_relation_types",
        "comparison_policy": (
            "C1 changes the candidate universe from mixed 156 to labeled 105. "
            "A score difference versus an existing mixed-156 report is NOT an algorithm gain."
        ),
        "excluded_topics": excluded_topics,
        "mode": "actual_A_B_C_offline",
        "case_text_view": "facts",
        "runtime": {"python": sys.version.split()[0], **_git_info(code_root)},
        "candidate_counts": {"rules": len(scoped.rules), "cases": len(scoped.cases)},
        "pre_scope_case_count": len(catalog.cases),
        "pre_scope_source_label_coverage": len(labels) / len(catalog.cases),
        "scoped_source_label_coverage": 1.0,
        "scope_policy": "original LexRel UUID membership, applied before search/graph construction",
        "scoped_case_ids": sorted(labels),
        "scope_label_mapping_sha256": _json_digest(
            {key: sorted(value) for key, value in sorted(labels.items())}
        ),
        "release_id": scoped.release_id,
        "evaluation_index_version": scoped.index_version,
        "inputs": {"sha256": before, "queries_sha256": query_hash, "query_manifest": manifest},
        "implementation_sha256": implementation,
        "case_search_text_sha256": {
            key: hashlib.sha256(case.search_text.encode("utf-8")).hexdigest()
            for key, case in scoped.cases.items()
        },
        "metric_definitions": {
            "k": list(ks),
            "recall": "relevant_retrieved / all source-label positives; unchanged",
            "ndcg": "gain=2^grade-1, discount=log2(rank+1); unchanged",
            "precision": "relevant_retrieved / requested K, including unfilled slots",
            "recall_ceiling": "min(K, positive_count) / positive_count; not normalized Recall",
            "source_label_coverage": "source-labeled returned candidates / returned candidates",
            "unjudged_ratio": "returned candidates without independent labels / returned candidates",
            "empty_ranking": "Recall and precision 0; returned-label coverage undefined (null)",
            "qrel_presence_vs_label_coverage": (
                "Existing metrics.judged_returned counts explicit positive qrel entries only. "
                "relation_at_k measures independent source-label availability, including "
                "source-annotated records without the queried relation."
            ),
        },
        "limitations": [
            (
                f"{len(queries)} of the original 13 fixed relation queries, evaluated on all 105 "
                "independently labeled LexRel cases; negative distractor candidates are retained."
            ),
            "This measures relation-type label retrieval, NOT case similarity or legal applicability.",
            "Label absence means negative only under the supplied dataset's annotation definition.",
            "Existing mixed-156 reports remain separate diagnostics; their scores are not interchangeable.",
            "A rules remain complete; actual A/B/C and BM25 run offline without LLM, vector or Neo4j.",
            "Facts-only text reduces metadata leakage but does not establish universal leakage freedom.",
            "These repeatedly inspected questions are a development benchmark, not a blind test.",
        ],
        **result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--queries", type=Path)
    parser.add_argument("--k", type=int, nargs="+", default=[5, 10, 20])
    parser.add_argument("--contract-only", action="store_true")
    parser.add_argument("--case-han-unigram-weight", type=float, default=0.25)
    parser.add_argument("--case-feedback", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_report(
            args.data_root, args.queries, k=args.k, contract_only=args.contract_only,
            case_han_unigram_weight=args.case_han_unigram_weight,
            case_feedback=args.case_feedback,
        )
        write_report(
            args.output,
            report,
            data_root=args.data_root,
            inputs=[args.queries] if args.queries else [],
        )
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        print(
            f"C1 relation evaluation failed ({type(exc).__name__}); no report published.",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "task": report["task"],
                "candidate_counts": report["candidate_counts"],
                "query_count": len(report["queries"]),
                "summary": report["summary"],
                "relation_summary": report["relation_summary"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
