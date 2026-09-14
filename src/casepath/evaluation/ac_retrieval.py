"""Evaluate the real A/B/C implementations offline with fixed, separate judgments.

This command does not load .env, start services or invoke any paid/network model.
It measures MemoryHybridSearch + MemoryGraphNavigator, not the live vector stack.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from casepath.adapters.conditions.semantic_condition_projector import SemanticConditionProjector
from casepath.adapters.retrieval.evaluation_trace import capture_retrieval_rankings
from casepath.adapters.retrieval.graph_navigator import MemoryGraphNavigator
from casepath.adapters.retrieval.hybrid_cypher import (
    HybridCypherCaseRetriever,
    HybridCypherRuleRetriever,
    HybridCypherSettings,
    _query_text,
)
from casepath.adapters.retrieval.retrieval_catalog import (
    MemoryHybridSearch,
    RetrievalCatalog,
    bind_context,
    bind_round,
    bind_scope,
    load_canonical_catalog,
    reset_context,
    reset_round,
    reset_scope,
)
from casepath.application.runtime_meta import (
    SessionRuntimeMeta,
    bind_runtime_meta,
    reset_runtime_meta,
)
from casepath.contracts import CaseIndexRecord, QueryState
from casepath.evaluation.case_text import CASE_TEXT_POLICY, build_case_search_text
from casepath.evaluation.metrics import aggregate_scores, score_ranking, validate_k
from casepath.ingestion.laws.jsonl import read_jsonl

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z")
_FIELDS = {"query_id", "module", "domain", "query_text", "qrels", "judgment_basis", "source_refs"}
_CANONICAL = (
    "canonical/rules/legal_sources.jsonl",
    "canonical/rules/provisions.jsonl",
    "canonical/rules/rules.jsonl",
    "canonical/rules/source_spans.jsonl",
    "canonical/cases/cases_l1.jsonl",
    "canonical/cases/cases_deep.jsonl",
    "manifests/cases.manifest.json",
    "manifests/civil_code.manifest.json",
)
_STAGES = {"A": ("a.final", "a.bm25"), "C": ("c.fused", "c.pool", "c.bm25")}


@dataclass(frozen=True)
class EvaluationQuery:
    query_id: str
    module: str
    domain: str
    query_text: str
    qrels: dict[str, int]
    judgment_basis: str
    source_refs: tuple[str, ...]
    exclude_ids: tuple[str, ...] = ()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            raise ValueError("blank JSONL record")
        row = json.loads(line, object_pairs_hook=_unique_object)
        if not isinstance(row, dict):
            raise TypeError("JSONL records must be objects")
        rows.append(row)
    if not rows:
        raise ValueError("empty JSONL input")
    return rows


def load_queries(path: Path, catalog: RetrievalCatalog) -> list[EvaluationQuery]:
    queries = []
    seen_ids, seen_texts = set(), set()
    for row in _read_jsonl(path):
        if not _FIELDS <= set(row) or set(row) - _FIELDS - {"exclude_ids"}:
            raise ValueError("query fields do not match the evaluation schema")
        qid = row["query_id"]
        if not isinstance(qid, str) or not _ID.fullmatch(qid) or qid in seen_ids:
            raise ValueError("invalid or duplicate query ID")
        if row["module"] not in _STAGES:
            raise ValueError("module must be A or C")
        for key in ("domain", "query_text", "judgment_basis"):
            if not isinstance(row[key], str) or not row[key].strip():
                raise ValueError("query text, domain and judgment basis must be nonempty")
        text_key = (row["module"], "".join(row["query_text"].split()))
        if text_key in seen_texts:
            raise ValueError("duplicate normalized query in a module")
        refs = row["source_refs"]
        if (
            not isinstance(refs, list)
            or not refs
            or any(not isinstance(ref, str) or not ref.strip() for ref in refs)
            or len(refs) != len(set(refs))
        ):
            raise ValueError("source_refs must be nonempty unique references")
        excluded = row.get("exclude_ids", [])
        if not isinstance(excluded, list):
            raise TypeError("exclude_ids must be a list")
        candidates = catalog.rules if row["module"] == "A" else catalog.cases
        score_ranking([], row["qrels"], candidate_ids=candidates, k=[1], exclude_ids=excluded)
        queries.append(
            EvaluationQuery(
                query_id=qid,
                module=row["module"],
                domain=row["domain"],
                query_text=row["query_text"],
                qrels=dict(row["qrels"]),
                judgment_basis=row["judgment_basis"],
                source_refs=tuple(refs),
                exclude_ids=tuple(excluded),
            )
        )
        seen_ids.add(qid)
        seen_texts.add(text_key)
    return queries


def facts_only_catalog(data_root: Path, catalog: RetrievalCatalog) -> RetrievalCatalog:
    """Replace only the evaluation copy's lexical text; keep graph evidence unchanged."""
    light = read_jsonl(data_root / "canonical/cases/cases_l1.jsonl", CaseIndexRecord)
    if len(light) != len(catalog.cases) or {item.case_id for item in light} != set(catalog.cases):
        raise ValueError("case text view does not cover exactly the canonical candidate space")
    texts = {item.case_id: build_case_search_text(item) for item in light}
    if any(not text.strip() for text in texts.values()):
        raise ValueError("facts-only case document is empty")
    cases = {key: replace(doc, search_text=texts[key]) for key, doc in catalog.cases.items()}
    view = replace(
        catalog,
        cases=cases,
        index_version=_json_digest(
            {
                "original_index_version": catalog.index_version,
                "strategy": CASE_TEXT_POLICY,
                "cases": texts,
            }
        ),
    )
    # Metadata and graph topology cannot become new query/label channels here.
    for key, doc in cases.items():
        if replace(doc, search_text=catalog.cases[key].search_text) != catalog.cases[key]:
            raise ValueError("evaluation text replacement changed graph evidence")
    return view


def _state(query: EvaluationQuery) -> QueryState:
    # No qrels, example IDs, source references or legal grades enter runtime state.
    return QueryState(
        session_id=f"eval.{query.query_id}",
        initial_query=query.query_text,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _score(query: EvaluationQuery, ids: tuple[str, ...], catalog: RetrievalCatalog, ks) -> dict:
    candidates = catalog.rules if query.module == "A" else catalog.cases
    metrics = score_ranking(
        ids, query.qrels, candidate_ids=candidates, k=ks, exclude_ids=query.exclude_ids
    )
    # IDs and scores are sufficient to reproduce metrics; no user/case body is published.
    excluded = set(query.exclude_ids)
    return {"ranked_ids": [key for key in ids if key not in excluded], "metrics": metrics}


def evaluate_queries(
    catalog: RetrievalCatalog,
    queries: list[EvaluationQuery],
    *,
    k=(5, 10, 20),
    case_han_unigram_weight: float = 0.25,
    case_feedback: bool = False,
) -> dict:
    ks = validate_k(k)
    search = MemoryHybridSearch(
        catalog, case_han_unigram_weight=case_han_unigram_weight, case_feedback=case_feedback
    )
    # The named BM25 control remains plain BM25, even when C uses lexical feedback.
    baseline_search = (
        MemoryHybridSearch(catalog, case_han_unigram_weight=case_han_unigram_weight)
        if case_feedback else search
    )
    navigator = MemoryGraphNavigator(catalog)
    rows = []
    for query in queries:
        # Additional slots permit self-removal before the requested scoring cutoff.
        a_extra = len(query.exclude_ids) if query.module == "A" else 0
        c_extra = len(query.exclude_ids) if query.module == "C" else 0
        settings = HybridCypherSettings(
            rule_top_k=max(ks) + a_extra,
            case_recall_top_k=max(30, max(ks) + c_extra),
            case_rerank_pool_size=max(20, max(ks) + c_extra),
        )
        rule_retriever = HybridCypherRuleRetriever(
            navigator,
            search,
            settings,
            catalog=catalog,
        )
        case_retriever = HybridCypherCaseRetriever(
            navigator,
            search,
            settings,
            catalog=catalog,
        )
        context_token = bind_context(catalog.context())
        round_token, scope_token = bind_round(None), bind_scope(None)
        runtime_token = bind_runtime_meta(SessionRuntimeMeta())
        try:
            with capture_retrieval_rankings() as trace:
                state = _state(query)
                rules = rule_retriever.retrieve(state)
                if trace.ids("a.final") != tuple(ref.object_id for ref in rules):
                    raise ValueError("A returned rankings do not match its trace")
                rankings = {}
                diagnostics = {"rule_count": len(rules)}
                if query.module == "A":
                    rankings["a.final"] = trace.ids("a.final")
                    baseline = baseline_search.search(
                        _query_text(state),
                        target="rule",
                        context=catalog.context(),
                        top_k=max(ks) + a_extra,
                    )
                    rankings["a.bm25"] = tuple(ref.object_id for ref in baseline)
                else:
                    if not rules:
                        # An actual empty A response is a measurable cascade failure, not a skip.
                        rankings["c.fused"], rankings["c.pool"] = (), ()
                        projected = state
                        diagnostics["cascade_status"] = "empty_A_no_C_candidates"
                    else:
                        projected = SemanticConditionProjector(catalog, None).project(state, rules)
                        bundle = case_retriever.retrieve(projected, rules)
                        rankings["c.fused"] = trace.ids("c.fused")
                        rankings["c.pool"] = trace.ids("c.pool")
                        diagnostics.update(
                            {
                                "cascade_status": "A_B_C_completed",
                                "projected_condition_count": len(projected.condition_states),
                                "pool_count": bundle.fused_case_count,
                                "undetermined_count": bundle.undetermined_case_count,
                                "role_output_ids": {
                                    "support": [ref.object_id for ref in bundle.support_case_refs],
                                    "limiting": [
                                        ref.object_id for ref in bundle.limiting_case_refs
                                    ],
                                    "boundary": [
                                        ref.object_id for ref in bundle.boundary_case_refs
                                    ],
                                },
                            }
                        )
                    # Identical projected query for C baseline, no gold condition injection.
                    baseline = baseline_search.search(
                        _query_text(projected),
                        target="case",
                        context=catalog.context(),
                        top_k=max(ks) + c_extra,
                    )
                    rankings["c.bm25"] = tuple(ref.object_id for ref in baseline)
                rows.append(
                    {
                        "query_id": query.query_id,
                        "module": query.module,
                        "domain": query.domain,
                        "judgment_basis": query.judgment_basis,
                        "query_sha256": hashlib.sha256(
                            query.query_text.encode("utf-8")
                        ).hexdigest(),
                        "qrels_sha256": _json_digest(query.qrels),
                        "settings": {**asdict(settings),
                                     "case_han_unigram_weight": case_han_unigram_weight,
                                     "case_feedback": case_feedback},
                        "diagnostics": diagnostics,
                        "rankings": {
                            stage: _score(query, ids, catalog, ks)
                            for stage, ids in rankings.items()
                        },
                    }
                )
        except Exception as exc:
            raise RuntimeError(
                f"evaluation failed at {query.query_id}; {type(exc).__name__}"
            ) from exc
        finally:
            reset_runtime_meta(runtime_token)
            reset_scope(scope_token)
            reset_round(round_token)
            reset_context(context_token)
    return {"queries": rows, "summary": summarize(rows, ks)}


def summarize(rows: list[dict], ks) -> dict:
    grouped, domains = defaultdict(list), defaultdict(lambda: defaultdict(list))
    bases = defaultdict(lambda: defaultdict(list))
    for row in rows:
        for stage, result in row["rankings"].items():
            grouped[stage].append(result["metrics"])
            domains[stage][row["domain"]].append(result["metrics"])
            bases[stage][row["judgment_basis"].split(":", 1)[0]].append(result["metrics"])
    return {
        stage: {
            **aggregate_scores(results, k=ks),
            "by_domain": {
                domain: aggregate_scores(items, k=ks)
                for domain, items in sorted(domains[stage].items())
            },
            "by_judgment_basis": {
                basis: aggregate_scores(items, k=ks)
                for basis, items in sorted(bases[stage].items())
            },
        }
        for stage, results in sorted(grouped.items())
    }


def evaluate_predictions(
    path: Path,
    catalog: RetrievalCatalog,
    queries: list[EvaluationQuery],
    *,
    k=(5, 10, 20),
) -> dict:
    """External rankings require exact query coverage; their live origin is not attested."""
    ks = validate_k(k)
    predictions = {}
    for row in _read_jsonl(path):
        if set(row) != {"query_id", "ranked_ids"}:
            raise ValueError("predictions require only query_id and ranked_ids")
        if not isinstance(row["query_id"], str) or row["query_id"] in predictions:
            raise ValueError("invalid or duplicate prediction query")
        if not isinstance(row["ranked_ids"], list):
            raise TypeError("ranked_ids must be a list")
        predictions[row["query_id"]] = row["ranked_ids"]
    if set(predictions) != {query.query_id for query in queries}:
        raise ValueError("predictions must cover every query exactly, including empty rankings")
    rows = []
    for query in queries:
        stage = "a.external" if query.module == "A" else "c.external"
        rows.append(
            {
                "query_id": query.query_id,
                "module": query.module,
                "domain": query.domain,
                "judgment_basis": query.judgment_basis,
                "query_sha256": hashlib.sha256(query.query_text.encode("utf-8")).hexdigest(),
                "qrels_sha256": _json_digest(query.qrels),
                "rankings": {stage: _score(query, tuple(predictions[query.query_id]), catalog, ks)},
            }
        )
    return {"queries": rows, "summary": summarize(rows, ks)}


def _git_info(root: Path) -> dict:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain", "--untracked-files=normal"],
                cwd=root,
                text=True,
                encoding="utf-8",
                stderr=subprocess.DEVNULL,
            ).strip()
        )
        return {"commit": commit, "dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}


def _implementation_hashes(root: Path) -> dict:
    paths = sorted((root / "src/casepath").rglob("*.py"))
    return {path.relative_to(root).as_posix(): _sha(path) for path in paths}


def _benchmark_manifest(queries_path: Path, data_root: Path, count: int) -> dict | None:
    path = queries_path.with_suffix(".manifest.json")
    if not path.exists():
        if queries_path.name == "ac_queries.jsonl":
            raise ValueError("the official ac_queries.jsonl requires its pinned manifest")
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(manifest, dict):
        raise ValueError("benchmark manifest must be an object")  # noqa: TRY004
    files = manifest.get("files")
    if not isinstance(files, dict) or queries_path.name not in files:
        raise ValueError("benchmark manifest does not describe this query file")
    item = files[queries_path.name]
    if not isinstance(item, dict) or not {"sha256", "records"} <= set(item):
        raise ValueError("benchmark query descriptor is incomplete")
    if not isinstance(item["sha256"], str) or type(item["records"]) is not int:
        raise ValueError("benchmark query hash and count have invalid types")
    raw = queries_path.read_bytes().replace(b"\r\n", b"\n")
    if item["sha256"] != hashlib.sha256(raw).hexdigest() or item["records"] != count:
        raise ValueError("benchmark query manifest does not match the query input")
    candidates = manifest.get("candidate_snapshots")
    annotations = manifest.get("annotation_sources", [])
    if not isinstance(candidates, list) or not isinstance(annotations, list):
        raise ValueError("benchmark snapshots and annotation sources must be lists")  # noqa: TRY004
    for snapshot in [*candidates, *annotations]:
        if not isinstance(snapshot, dict) or not {"path", "sha256", "records"} <= set(snapshot):
            raise ValueError("benchmark snapshot descriptor is incomplete")
        if not isinstance(snapshot["path"], str) or not isinstance(snapshot["sha256"], str):
            raise ValueError("benchmark snapshot path and hash must be strings")  # noqa: TRY004
        relative = snapshot["path"]
        # Allow either repository-relative data/... or data-root-relative paths.
        relative = relative.removeprefix("data/")
        target = (data_root / relative).resolve()
        if not target.is_relative_to(data_root.resolve()) or target.suffix not in {
            ".jsonl",
            ".json",
        }:
            raise ValueError("benchmark manifest candidate path escapes the data root")
        body = target.read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(body).hexdigest() != snapshot["sha256"]:
            raise ValueError("benchmark candidate snapshot changed")
        if target.suffix == ".json":
            rows = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
            if not isinstance(rows, list):
                raise ValueError("annotation JSON input must be a record array")
        else:
            rows = _read_jsonl(target)
        if type(snapshot["records"]) is not int or len(rows) != snapshot["records"]:
            raise ValueError("benchmark candidate snapshot count changed")
    return {"sha256": _sha(path), "query_digest_policy": "LF-normalized bytes"}


def build_report(
    data_root: Path,
    queries_path: Path,
    *,
    k=(5, 10, 20),
    predictions: Path | None = None,
    case_text_view: str = "facts",
    case_feedback: bool = False,
) -> dict:
    ks = validate_k(k)
    data_root = data_root.resolve()
    pinned = {rel: _sha(data_root / rel) for rel in _CANONICAL}
    query_hash = _sha(queries_path)
    prediction_hash = _sha(predictions) if predictions else None
    if case_text_view not in {"facts", "canonical"}:
        raise ValueError("case_text_view must be facts or canonical")
    catalog = load_canonical_catalog(data_root)
    if case_text_view == "facts":
        catalog = facts_only_catalog(data_root, catalog)
    queries = load_queries(queries_path, catalog)
    benchmark_manifest = _benchmark_manifest(queries_path, data_root, len(queries))
    code_root = Path(__file__).resolve().parents[3]
    code_hashes = _implementation_hashes(code_root)
    result = (
        evaluate_predictions(predictions, catalog, queries, k=ks)
        if predictions
        else evaluate_queries(catalog, queries, k=ks, case_feedback=case_feedback)
    )
    if pinned != {rel: _sha(data_root / rel) for rel in _CANONICAL}:
        raise ValueError("canonical inputs changed during evaluation")
    if query_hash != _sha(queries_path) or (
        predictions is not None and prediction_hash != _sha(predictions)
    ):
        raise ValueError("evaluation inputs changed during the run")
    if code_hashes != _implementation_hashes(code_root):
        raise ValueError("implementation changed during the run")
    if benchmark_manifest != _benchmark_manifest(queries_path, data_root, len(queries)):
        raise ValueError("benchmark manifest changed during the run")
    report = {
        "schema_version": "1.0",
        "protocol": "casepath.ac.offline.v1",
        "runtime": {"python": sys.version.split()[0], **_git_info(code_root)},
        "mode": "external_rankings_unattested" if predictions else "actual_A_B_C_offline",
        "case_text_view": case_text_view,
        "metrics": {
            "k": list(ks),
            "recall_positive_grade": ">0",
            "ndcg_gain": "2^grade-1",
            "ndcg_discount": "log2(rank+1)",
            "averaging": "query macro; no-positive queries excluded and separately counted",
            "unjudged": "grade 0 for these incomplete silver judgments, not proven irrelevant",
            "excluded_ids": "removed from retrieved and ideal rankings before top-k",
        },
        "retrieval_configuration": {
            "A": (
                "HybridCypherRuleRetriever; rule_top_k=max(k) plus exclusions; "
                "recall=max(rule_top_k, rule_recall_top_k), capped to corpus"
            ),
            "rule_recall_top_k": HybridCypherSettings().rule_recall_top_k,
            "B": "SemanticConditionProjector(language_model=None), lexical fallback",
            "C": "HybridCypherCaseRetriever; memory graph + BM25",
            "case_feedback": case_feedback,
            "baseline": "MemoryHybridSearch, same corpus and actual stage query text",
            "vectors": False,
            "network": False,
            "live_neo4j": False,
            "case_text": (
                CASE_TEXT_POLICY + "; no appended title/cause/keywords/claims/annotations"
                if case_text_view == "facts"
                else "canonical production text: title/cause/keywords/fact_prefix/findings; "
                "annotation enriched"
            ),
            "graph": "unchanged canonical conditions, applies and cites; no qrel/label injection",
            "c.fused": (
                "query-channel RRF then unordered graph-only fallback, before role balancing; "
                "primary C ranking metric"
            ),
            "c.pool": "role-interleaved bounded pool; secondary operational-order metric",
            "roles": "support/limiting/boundary are not concatenated into a relevance ranking",
        },
        "candidate_counts": {"rules": len(catalog.rules), "cases": len(catalog.cases)},
        "release_id": catalog.release_id,
        "evaluation_index_version": catalog.index_version,
        "inputs": {
            "canonical_sha256": pinned,
            "queries_sha256": query_hash,
            "predictions_sha256": prediction_hash,
            "benchmark_manifest": benchmark_manifest,
        },
        "implementation_sha256": code_hashes,
        "case_search_text_sha256": {
            key: hashlib.sha256(doc.search_text.encode("utf-8")).hexdigest()
            for key, doc in sorted(catalog.cases.items())
        },
        "limitations": [
            "Development silver benchmark, not an exhaustive expert gold or held-out test set.",
            "LexRel relation labels measure topic similarity, not legal applicability or role.",
            "Facts-only text reduces metadata leakage; source facts can still contain legal language.",
            "The graph retains production evidence links; no guarantee of globally leakage-free data.",
            "Offline defaults do not estimate paid LLM/vector/Neo4j live retrieval quality.",
            "C depends on retrieved A rules and lexical B; no gold rules or conditions are injected.",
        ],
        **result,
    }
    if predictions:
        report["limitations"].append(
            "External rankings are scored, not certified as live executions."
        )
    if case_text_view == "canonical":
        report["limitations"].append(
            "Canonical case text includes LexRel label-derived metadata: label leakage risk. "
            "Its topic-label scores must not be presented as leakage-free relevance quality."
        )
    return report


def write_report(output: Path, report: dict, *, data_root: Path, inputs=()) -> None:
    """Reports only; reject symlink escapes and never overwrite different evidence."""
    output = output.resolve()
    declared_reports_root = data_root.resolve() / "reports"
    reports_root = declared_reports_root.resolve()
    if reports_root != declared_reports_root:
        raise ValueError("data/reports must not redirect through a symlink or junction")
    if output.suffix.lower() != ".json" or not output.is_relative_to(reports_root):
        raise ValueError("output must be a JSON file inside data/reports")
    if output in {Path(path).resolve() for path in inputs}:
        raise ValueError("output would overwrite an input")
    content = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    if output.exists():
        if output.read_text(encoding="utf-8") == content:
            return
        raise ValueError("refusing to overwrite a different existing report")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--queries", type=Path, default=Path("data/evaluation/ac_queries.jsonl"))
    parser.add_argument("--k", nargs="+", type=int, default=[5, 10, 20])
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--case-text-view", choices=("facts", "canonical"), default="facts")
    parser.add_argument("--case-feedback", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_report(
            args.data_root,
            args.queries,
            k=args.k,
            predictions=args.predictions,
            case_text_view=args.case_text_view,
            case_feedback=args.case_feedback,
        )
        write_report(
            args.output,
            report,
            data_root=args.data_root,
            inputs=[args.queries, *([args.predictions] if args.predictions else [])],
        )
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        print(
            f"A/C evaluation failed ({type(exc).__name__}); no successful report written.",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "mode": report["mode"],
                "queries": len(report["queries"]),
                "summary": report["summary"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
