"""Source-grounded C2 development benchmark: freeze a pool, then score judgments.

No model/network calls. Labels never enter QueryState or the retrieval catalog.
This is pooled, assistant-reviewed silver data, not an expert/held-out gold set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

from casepath.adapters.retrieval.retrieval_catalog import load_canonical_catalog
from casepath.evaluation.ac_retrieval import (
    _CANONICAL,
    EvaluationQuery,
    _implementation_hashes,
    _read_jsonl,
    _sha,
    evaluate_queries,
    facts_only_catalog,
    write_report,
)
from casepath.evaluation.metrics import aggregate_scores, score_ranking, validate_k

RUBRIC = {
    "3": "核心事实及待比较争点高度一致，且未发现使该比较失效的关键差异。",
    "2": "存在共同的具体争点；重要条件不同但能形成有价值的对照，不要求裁判结果相同。",
    "1": "同一宽泛纠纷方向，缺少待比较关键条件，仅提供背景。",
    "0": "材料显示争点和关键事实不同，没有可说明的比较价值。",
    "null": "材料不足或存在无法排除的歧义，未完成相关性判断；不得自动当成不相关。",
}
QUERY_FIELDS = {
    "query_id", "query_text", "domain", "comparison_focus", "rubric_notes", "source_case_ids",
}
PRIMARY = ("c.bm25", "c.fused", "c.pool")


def _prefix(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"c2(?:[._-][A-Za-z0-9]+)*", value):
        raise ValueError("artifact prefix must be a safe c2 version name")
    return value


def _artifact(path: Path, value: object, *, jsonl: bool = False) -> None:
    """Create-only generated artifacts, including idempotent rebuilds."""
    if path.is_symlink() or path.parent.resolve() != path.parent.absolute():
        raise ValueError("artifact and its parent cannot redirect through links")
    if jsonl:
        content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in value)
    else:
        content = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") == content:
            return
        raise ValueError(f"refusing to overwrite a different artifact: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def load_scenarios(paths: list[Path], candidates: set[str]) -> list[dict]:
    rows = [row for path in paths for row in _read_jsonl(path)]
    seen_ids, seen_texts = set(), set()
    for row in rows:
        if set(row) != QUERY_FIELDS:
            raise ValueError("C2 scenario fields mismatch")
        for key in ("query_id", "query_text", "domain", "rubric_notes"):
            if not isinstance(row[key], str) or not row[key].strip():
                raise ValueError("C2 text fields must be nonempty")
        if not row["query_id"].startswith("C2.") or row["query_id"] in seen_ids:
            raise ValueError("invalid or duplicate C2 query ID")
        text_key = "".join(row["query_text"].split())
        if text_key in seen_texts:
            raise ValueError("duplicate C2 query text")
        for field in ("comparison_focus", "source_case_ids"):
            values = row[field]
            if (not isinstance(values, list) or not values
                    or any(not isinstance(v, str) or not v.strip() for v in values)
                    or len(set(values)) != len(values)):
                raise ValueError("C2 focus/source IDs must be unique nonempty strings")
        if not set(row["source_case_ids"]) <= candidates:
            raise ValueError("C2 scenario references an unknown source case")
        seen_ids.add(row["query_id"])
        seen_texts.add(text_key)
    return sorted(rows, key=lambda row: row["query_id"])


def _queries(scenarios: list[dict]) -> list[EvaluationQuery]:
    return [
        EvaluationQuery(
            query_id=row["query_id"], module="C", domain=row["domain"],
            query_text=row["query_text"], qrels={},
            judgment_basis="condition_comparison_development_silver",
            source_refs=(),
        )
        for row in scenarios
    ]


def prepare(
    data_root: Path, parts: list[Path], *, depth: int = 10, scope_config: Path | None = None,
    artifact_prefix: str = "c2", previous_pool: Path | None = None,
    case_han_unigram_weight: float = 0.25,
    case_feedback: bool = False,
) -> dict:
    """Freeze scenarios before retrieving; build a rank-hidden union for assessment."""
    validate_k([depth])
    artifact_prefix = _prefix(artifact_prefix)
    data_root = data_root.resolve()
    evaluation = data_root / "evaluation"
    if evaluation.resolve() != evaluation:
        raise ValueError("evaluation output root must not redirect")
    source_inputs = [*parts, *([scope_config] if scope_config else []),
                     *([previous_pool] if previous_pool else [])]
    input_hashes = {path.resolve(): _sha(path) for path in source_inputs}
    pinned = {rel: _sha(data_root / rel) for rel in _CANONICAL}
    catalog = load_canonical_catalog(data_root)
    scenarios = load_scenarios(parts, set(catalog.cases))
    scope = {"scope": "all_authored_scenarios"}
    if scope_config is not None:
        scope = json.loads(scope_config.read_text(encoding="utf-8"))
        all_ids = {row["query_id"] for row in scenarios}
        included, excluded = scope["included_query_ids"], scope["excluded_query_ids"]
        if (not included or len(set(included)) != len(included)
                or len(set(excluded)) != len(excluded) or set(included) & set(excluded)
                or set(included) | set(excluded) != all_ids):
            raise ValueError("scope must partition all authored query IDs")
        scenarios = [row for row in scenarios if row["query_id"] in included]
        scope = {**scope, "config_sha256": _sha(scope_config)}
    query_path = evaluation / f"{artifact_prefix}_queries.jsonl"
    _artifact(query_path, scenarios, jsonl=True)
    query_hash = _sha(query_path)
    code_root = Path(__file__).resolve().parents[3]
    implementation = _implementation_hashes(code_root)
    results = {}
    for view in ("facts", "canonical"):
        view_catalog = facts_only_catalog(data_root, catalog) if view == "facts" else catalog
        # Use @20 budgets consistently with existing A/C evaluation; pool depth is independent.
        result = evaluate_queries(
            view_catalog, _queries(scenarios), k=(5, 10, 20),
            case_han_unigram_weight=case_han_unigram_weight,
            case_feedback=case_feedback,
        )
        results[view] = {
            row["query_id"]: {
                "rankings": {s: row["rankings"][s]["ranked_ids"] for s in PRIMARY},
                "settings": row["settings"], "diagnostics": row["diagnostics"],
            }
            for row in result["queries"]
        }
    old_pools = {}
    if previous_pool:
        previous = _read_jsonl(previous_pool)
        if (len(previous) != len(scenarios)
                or {r["query_id"] for r in previous} != {r["query_id"] for r in scenarios}):
            raise ValueError("previous pool must cover exactly the same queries")
        for row in previous:
            if (set(row) != {"query_id", "case_ids"} or not row["case_ids"]
                    or len(set(row["case_ids"])) != len(row["case_ids"])
                    or not set(row["case_ids"]) <= catalog.cases.keys()):
                raise ValueError("previous pool has invalid case IDs")
            old_pools[row["query_id"]] = set(row["case_ids"])
    pool_rows = []
    for row in scenarios:
        qid = row["query_id"]
        ids = set(row["source_case_ids"])
        ids.update(old_pools.get(qid, set()))
        for stage in PRIMARY:
            ids.update(results["facts"][qid]["rankings"][stage][:depth])
        # Additional lexical view diversifies the pool; metadata labels are NOT relevance grades.
        ids.update(results["canonical"][qid]["rankings"]["c.bm25"][:depth])
        # Presentation does not expose retrieval system, score, rank, or anchor membership.
        ids = sorted(ids, key=lambda key: hashlib.sha256(f"{qid}\0{key}".encode()).hexdigest())
        pool_rows.append({"query_id": qid, "case_ids": ids})
    if pinned != {rel: _sha(data_root / rel) for rel in _CANONICAL}:
        raise ValueError("canonical data changed while preparing C2")
    if query_hash != _sha(query_path) or implementation != _implementation_hashes(code_root):
        raise ValueError("query/implementation changed while preparing C2")
    if input_hashes != {path.resolve(): _sha(path) for path in source_inputs}:
        raise ValueError("scenario inputs changed while preparing C2")
    pool_path = evaluation / f"{artifact_prefix}_pool.jsonl"
    _artifact(pool_path, pool_rows, jsonl=True)
    report = {
        "protocol": "casepath.c2.pooled_development.v1",
        "depth": depth, "query_count": len(scenarios),
        "pair_count": sum(len(row["case_ids"]) for row in pool_rows),
        "candidate_ids": sorted(catalog.cases),
        "queries_sha256": query_hash, "pool_sha256": _sha(pool_path),
        "canonical_sha256": pinned, "implementation_sha256": implementation,
        "rubric": RUBRIC,
        "scope": scope,
        "pool_policy": "facts BM25/fused/role-pool top-depth + canonical BM25 top-depth + source anchors",
        "source_anchors": "query construction sources included for review, NOT automatic positives",
        "network": False, "vectors": False,
        "results": results,
        "case_han_unigram_weight": case_han_unigram_weight,
        "case_feedback": case_feedback,
        "previous_pool_sha256": _sha(previous_pool) if previous_pool else None,
    }
    write_report(data_root / f"reports/{artifact_prefix}_pool.runs.json", report, data_root=data_root)
    return {"queries": len(scenarios), "pairs": report["pair_count"], "depth": depth}


def load_judgments(paths: list[Path], pool: list[dict], cases: dict[str, dict]) -> dict:
    seen_queries = set()
    for row in pool:
        if (set(row) != {"query_id", "case_ids"} or row["query_id"] in seen_queries
                or not isinstance(row["case_ids"], list) or not row["case_ids"]
                or len(set(row["case_ids"])) != len(row["case_ids"])
                or not set(row["case_ids"]) <= set(cases)):
            raise ValueError("invalid, duplicate or unknown C2 pool entry")
        seen_queries.add(row["query_id"])
    expected = {(row["query_id"], cid) for row in pool for cid in row["case_ids"]}
    judgments = {}
    required = {"query_id", "case_id", "grade", "rationale", "evidence"}
    for path in paths:
        for row in _read_jsonl(path):
            if set(row) != required:
                raise ValueError("C2 judgment fields mismatch")
            key = (row["query_id"], row["case_id"])
            if key not in expected or key in judgments:
                raise ValueError("unknown or duplicate C2 judgment pair")
            grade = row["grade"]
            if grade is not None and (type(grade) is not int or not 0 <= grade <= 3):
                raise ValueError("C2 grade must be 0..3 or null")
            if not isinstance(row["rationale"], str) or len(row["rationale"].strip()) < 8:
                raise ValueError("C2 judgment requires a substantive rationale")
            evidence = row["evidence"]
            if not isinstance(evidence, list) or not evidence:
                raise ValueError("all C2 grades require source text evidence")
            for quote in evidence:
                if not isinstance(quote, dict) or set(quote) != {"field", "quote"}:
                    raise ValueError("invalid C2 evidence entry")
                field, text = quote["field"], quote["quote"]
                if (field not in {"facts_text", "reasoning_text"}
                        or not isinstance(text, str) or not text.strip()
                        or text not in cases[row["case_id"]].get(field, "")):
                    raise ValueError("C2 evidence quote is not present in source text")
            judgments[key] = row
    if set(judgments) != expected:
        raise ValueError("missing C2 judgments; do not silently score an incomplete pool")
    return judgments


def score(
    data_root: Path, judgment_paths: list[Path], *, k=(5, 10), artifact_prefix="c2",
    baseline_run: Path | None = None,
) -> dict:
    """Score frozen runs: top-k must fit the reviewed pool depth, never drop unjudged hits."""
    ks = validate_k(k)
    artifact_prefix = _prefix(artifact_prefix)
    pool_path, query_path = (
        data_root / f"evaluation/{artifact_prefix}_pool.jsonl",
        data_root / f"evaluation/{artifact_prefix}_queries.jsonl"
    )
    run_path = data_root / f"reports/{artifact_prefix}_pool.runs.json"
    resolved_judgments = [path.resolve() for path in judgment_paths]
    if len(set(resolved_judgments)) != len(resolved_judgments):
        raise ValueError("duplicate judgment input path")
    inputs = [run_path, pool_path, query_path, *judgment_paths,
              *([baseline_run] if baseline_run else []),
              *(data_root / rel for rel in _CANONICAL)]
    pinned = {path.resolve(): _sha(path) for path in inputs}
    code_root = Path(__file__).resolve().parents[3]
    scoring_implementation = _implementation_hashes(code_root)
    runs = json.loads(run_path.read_text(encoding="utf-8"))
    validate_k([runs["depth"]])
    if max(ks) > runs["depth"]:
        raise ValueError("requested k exceeds reviewed pooling depth")
    if _sha(pool_path) != runs["pool_sha256"] or _sha(query_path) != runs["queries_sha256"]:
        raise ValueError("C2 pool/query fingerprint changed")
    if runs["canonical_sha256"] != {rel: _sha(data_root / rel) for rel in _CANONICAL}:
        raise ValueError("C2 source snapshot changed")
    ranking_runs = runs
    if baseline_run:
        ranking_runs = json.loads(baseline_run.read_text(encoding="utf-8"))
        for field in ("queries_sha256", "canonical_sha256", "candidate_ids"):
            if ranking_runs[field] != runs[field]:
                raise ValueError("paired baseline must use identical queries/data/candidate space")
        if set(ranking_runs["results"]["facts"]) != set(runs["results"]["facts"]):
            raise ValueError("paired baseline query coverage differs")
    cases = {r["case_id"]: r for r in _read_jsonl(data_root / "canonical/cases/cases_l1.jsonl")}
    pool = _read_jsonl(pool_path)
    scenarios = _read_jsonl(query_path)
    query_ids = {row["query_id"] for row in scenarios}
    if (len(query_ids) != len(scenarios) or len(pool) != len(scenarios)
            or {row["query_id"] for row in pool} != query_ids
            or set(runs["results"]["facts"]) != query_ids
            or set(runs["candidate_ids"]) != set(cases)):
        raise ValueError("C2 scenarios, pool and frozen runs must have identical coverage")
    pools = {row["query_id"]: set(row["case_ids"]) for row in pool}
    judgments = load_judgments(judgment_paths, pool, cases)
    rows, summary = [], {}
    for scenario in scenarios:
        qid = scenario["query_id"]
        qrels = {cid: row["grade"] for (query_id, cid), row in judgments.items()
                 if query_id == qid and row["grade"] is not None}
        unresolved = [cid for (query_id, cid), row in judgments.items()
                      if query_id == qid and row["grade"] is None]
        comparison_positives = sum(grade >= 2 for grade in qrels.values())
        rankings = {}
        for stage in PRIMARY:
            ranked_ids = ranking_runs["results"]["facts"][qid]["rankings"][stage]
            if not set(ranked_ids[:max(ks)]) <= pools[qid]:
                raise ValueError("C2 ranked top-k contains candidates outside the reviewed pool")
            metrics = score_ranking(ranked_ids, qrels, candidate_ids=cases, k=ks)
            if unresolved:
                # Unresolved pooled relevance can affect both gains and the Recall denominator.
                # Do not publish a point estimate or silently remove the uncertain document.
                metrics["status"] = "incomplete_judgments"
                for values in metrics["at_k"].values():
                    values["recall"] = values["ndcg"] = None
            for limit in ks:
                comparison_hits = sum(qrels.get(cid, 0) >= 2 for cid in ranked_ids[:limit])
                metrics["at_k"][str(limit)]["comparison_recall"] = (
                    comparison_hits / comparison_positives
                    if comparison_positives and not unresolved else None
                )
                metrics["at_k"][str(limit)]["judged_fraction"] = (
                    metrics["at_k"][str(limit)]["judged_returned"] / min(limit, len(ranked_ids))
                    if ranked_ids else None
                )
            rankings[stage] = {"ranked_ids": ranked_ids, "metrics": metrics}
        rows.append({
            "query_id": qid, "domain": scenario["domain"],
            "qrels": qrels, "unresolved_case_ids": unresolved, "rankings": rankings,
            "comparison_positive_count": comparison_positives,
        })
    for stage in PRIMARY:
        summary[stage] = aggregate_scores([row["rankings"][stage]["metrics"] for row in rows], k=ks)
        summary[stage]["incomplete_query_count"] = sum(bool(row["unresolved_case_ids"]) for row in rows)
        summary[stage]["no_positive_query_count"] = sum(
            row["rankings"][stage]["metrics"]["status"] == "no_positive_judgments" for row in rows
        )
        summary[stage]["empty_ranking_query_count"] = sum(
            not row["rankings"][stage]["ranked_ids"] for row in rows
        )
        summary[stage]["macro_judged_fraction"] = {}
        summary[stage]["comparison_scored_query_count"] = sum(
            row["comparison_positive_count"] > 0 and not row["unresolved_case_ids"] for row in rows
        )
        summary[stage]["comparison_no_positive_query_count"] = sum(
            row["comparison_positive_count"] == 0 and not row["unresolved_case_ids"] for row in rows
        )
        for limit in ks:
            recalls = [row["rankings"][stage]["metrics"]["at_k"][str(limit)]["comparison_recall"]
                       for row in rows]
            recalls = [value for value in recalls if value is not None]
            summary[stage]["macro_at_k"][str(limit)]["comparison_recall"] = (
                math.fsum(recalls) / len(recalls) if recalls else None
            )
            fractions = [
                row["rankings"][stage]["metrics"]["at_k"][str(limit)]["judged_fraction"]
                for row in rows
                if row["rankings"][stage]["ranked_ids"]
            ]
            summary[stage]["macro_judged_fraction"][str(limit)] = (
                math.fsum(fractions) / len(fractions) if fractions else None
            )
    report = {
        "protocol": "casepath.c2.pooled_development.v1", "network": False, "vectors": False,
        "pool_run_sha256": _sha(run_path), "queries_sha256": _sha(query_path),
        "ranking_run_sha256": _sha(baseline_run or run_path),
        "ranking_implementation_sha256": ranking_runs["implementation_sha256"],
        "comparison_role": "baseline_rescored_on_same_pool" if baseline_run else "candidate",
        "pool_sha256": _sha(pool_path),
        "judgments_sha256": {f"{i}:{p.name}": pinned[p.resolve()]
                             for i, p in enumerate(judgment_paths)},
        "scoring_implementation_sha256": scoring_implementation,
        "candidate_count": len(cases), "pair_count": len(judgments),
        "unresolved_pair_count": sum(row["grade"] is None for row in judgments.values()),
        "rubric": RUBRIC,
        "scope": runs["scope"],
        "metric_definitions": {
            "recall": "reviewed-pool positives with grade >= 1, including background",
            "comparison_recall": "reviewed-pool positives with grade >= 2; primary useful-case metric",
            "ndcg": "graded gains 2^grade-1; backgrounds retain lower gain",
        },
        "judgment_method": "assistant source-text review; development silver, not human expert gold",
        "limitations": [
            "Source-anchored authored scenarios, not held-out real user traffic.",
            "Recall denominator contains reviewed pool positives, not all relevant cases in corpus.",
            ("Any unresolved pool judgment suppresses that query's Recall/nDCG point estimates; "
             "incomplete counts and judged coverage remain explicit."),
            "No vector/LLM service was called or evaluated.",
            "c.pool is an operational role-diverse order, not a pure similarity ranking.",
        ],
        "queries": rows, "summary": summary,
    }
    if pinned != {path.resolve(): _sha(path) for path in inputs}:
        raise ValueError("C2 evaluation inputs changed during scoring")
    if scoring_implementation != _implementation_hashes(code_root):
        raise ValueError("C2 scoring implementation changed during scoring")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "score"))
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--inputs", type=Path, nargs="+", required=True)
    parser.add_argument("--depth", type=int, default=10)
    parser.add_argument("--k", type=int, nargs="+", default=[5, 10])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--scope-config", type=Path)
    parser.add_argument("--artifact-prefix", default="c2")
    parser.add_argument("--previous-pool", type=Path)
    parser.add_argument("--case-han-unigram-weight", type=float, default=0.25)
    parser.add_argument("--case-feedback", action="store_true")
    parser.add_argument("--baseline-run", type=Path)
    args = parser.parse_args(argv)
    if args.action == "prepare":
        result = prepare(
            args.data_root, args.inputs, depth=args.depth, scope_config=args.scope_config,
            artifact_prefix=args.artifact_prefix, previous_pool=args.previous_pool,
            case_han_unigram_weight=args.case_han_unigram_weight,
            case_feedback=args.case_feedback,
        )
    else:
        if args.output is None:
            parser.error("score requires --output")
        result = score(
            args.data_root, args.inputs, k=args.k, artifact_prefix=args.artifact_prefix,
            baseline_run=args.baseline_run,
        )
        write_report(args.output, result, data_root=args.data_root, inputs=args.inputs)
        result = {"queries": len(result["queries"]), "pairs": result["pair_count"],
                  "unresolved": result["unresolved_pair_count"], "summary": result["summary"]}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
