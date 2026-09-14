"""Bounded offline PRF experiment; not a production default or held-out test."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from casepath.adapters.retrieval.retrieval_catalog import (
    MemoryHybridSearch,
    load_canonical_catalog,
    scored_reference,
    tokenize,
)
from casepath.contracts import ScoreComponent
from casepath.evaluation import ac_retrieval as evaluation
from casepath.evaluation.relation_coverage import (
    relation_queries,
    scope_catalog,
    select_query_scope,
    source_labels,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PRESETS = (
    {"name": "baseline", "feedback_docs": 0, "terms": 0, "alpha": 0.0},
    {"name": "prf_top3_terms12_a015", "feedback_docs": 3, "terms": 12, "alpha": 0.15},
    {"name": "prf_top5_terms20_a020", "feedback_docs": 5, "terms": 20, "alpha": 0.20},
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def constructor(preset):
    class FeedbackSearch(MemoryHybridSearch):
        def search(self, text, *, target, context, top_k):
            # Use the original context validation and unchanged rule retrieval.
            original = super().search(text, target=target, context=context, top_k=top_k)
            if target != "case" or not preset["feedback_docs"] or not original:
                return original
            index = self.case_index
            base = index.search(text, index.n)
            query_terms = set(tokenize(text))
            support = defaultdict(float)
            for oid, doc_score in base[:preset["feedback_docs"]]:
                frequencies = Counter(index.tokens[oid])
                length = len(index.tokens[oid]) or 1
                for term, frequency in frequencies.items():
                    df = len(index._postings[term])
                    if (
                        len(term) == 2
                        and all("\u4e00" <= char <= "\u9fff" for char in term)
                        and term not in query_terms
                        and 2 <= df <= 0.35 * index.n
                    ):
                        support[term] += doc_score / base[0][1] * frequency / length * index.idf[term]
            terms = sorted(support, key=lambda term: (-support[term], term))[:preset["terms"]]
            if not terms:
                return original
            expansion = index.search(" ".join(terms), index.n)
            scores = {oid: value / base[0][1] for oid, value in base}
            if expansion:
                for oid, value in expansion:
                    scores[oid] = scores.get(oid, 0.0) + preset["alpha"] * value / expansion[0][1]
            ranked = sorted(scores.items(), key=lambda row: (-row[1], row[0]))[:top_k]
            return [
                scored_reference(
                    oid, value, channels=["bm25"],
                    components=[ScoreComponent(name="bm25", value=value, explanation="offline PRF trial")],
                    reasons=["offline PRF trial"],
                ) for oid, value in ranked
            ]
    return FeedbackSearch


def c2_observation(run, judgments):
    """Do not produce a complete Recall/nDCG with unjudged candidates as zero."""
    observed = []
    aggregate = {stage: {str(k): {"known_useful_hits": 0, "new_unjudged": 0, "returned": 0}
                         for k in (5, 10)} for stage in ("c.bm25", "c.fused", "c.pool")}
    for row in run["queries"]:
        grades = judgments[row["query_id"]]
        out = {"query_id": row["query_id"], "rankings": {}}
        for stage, ranking in row["rankings"].items():
            ids = ranking["ranked_ids"]
            at_k = {}
            for k in (5, 10):
                top = ids[:k]
                values = {
                    "known_useful_hits": sum(oid in grades and grades[oid] >= 2 for oid in top),
                    "new_unjudged": sum(oid not in grades for oid in top),
                    "returned": len(top),
                }
                for key, value in values.items():
                    aggregate[stage][str(k)][key] += value
                at_k[str(k)] = values
            out["rankings"][stage] = {"ranked_ids": ids, "observed_at_k": at_k}
        observed.append(out)
    return {"queries": observed, "summary": aggregate,
            "warning": "Known useful hits only, not full-corpus recall. Unknown candidates are not scored as irrelevant."}


def main():
    paths = list((ROOT / "src/casepath/adapters/retrieval").glob("*.py"))
    paths += list((ROOT / "src/casepath/evaluation").glob("*.py"))
    paths += [ROOT / "src/casepath/adapters/conditions/semantic_condition_projector.py", Path(__file__)]
    paths += [DATA / relative for relative in evaluation._CANONICAL]
    paths += [DATA / "evaluation/ac_queries.jsonl", DATA / "evaluation/c2_queries.jsonl"]
    label_paths = [DATA / f"evaluation/c2_judgments.{part}.jsonl" for part in ("part1", "part2", "u05_extra")]
    paths += label_paths
    hashes = {str(path.relative_to(ROOT)): sha(path) for path in paths}
    catalog = load_canonical_catalog(DATA)
    facts = evaluation.facts_only_catalog(DATA, catalog)
    queries = evaluation.load_queries(DATA / "evaluation/ac_queries.jsonl", catalog)
    labels = source_labels(DATA)
    topics, relations = relation_queries(DATA, queries, labels)
    contract, _ = select_query_scope(topics, relations, contract_only=True)
    scoped = scope_catalog(facts, labels)
    judgments = defaultdict(dict)
    for path in label_paths:
        for row in read_rows(path):
            qid, cid = row["query_id"], row["case_id"]
            if cid in judgments[qid] or type(row["grade"]) is not int:
                raise ValueError("duplicate or unresolved judgment")
            judgments[qid][cid] = row["grade"]
    c2 = [evaluation.EvaluationQuery(
        query_id=row["query_id"], module="C", domain=row["domain"], query_text=row["query_text"],
        qrels={}, judgment_basis="ranking_only_pending_additional_judgments", source_refs=(),
    ) for row in read_rows(DATA / "evaluation/c2_queries.jsonl")]
    if len(contract) != 10 or len(c2) != 13 or sum(map(len, judgments.values())) != 172:
        raise ValueError("unexpected frozen evaluation size")
    runs = []
    original_class = evaluation.MemoryHybridSearch
    try:
        for preset in PRESETS:
            evaluation.MemoryHybridSearch = constructor(preset)
            mixed = evaluation.evaluate_queries(facts, contract)
            c1 = evaluation.evaluate_queries(scoped, contract)
            c2_run = c2_observation(evaluation.evaluate_queries(facts, c2), judgments)
            runs.append({"preset": preset, "mixed156": mixed, "c1_labeled105": c1, "c2_observed172": c2_run})
            print(json.dumps({"preset": preset, "mixed156": mixed["summary"], "c1_labeled105": c1["summary"],
                              "c2_observed172": c2_run["summary"]}, ensure_ascii=False), flush=True)
    finally:
        evaluation.MemoryHybridSearch = original_class
    after = {str(path.relative_to(ROOT)): sha(path) for path in paths}
    if hashes != after:
        raise RuntimeError("source or frozen benchmark changed during trial")
    payload = {"presets_declared_before_execution": PRESETS, "source_sha256": hashes,
               "scope": "contract themes: mixed 156 and source-labeled C1 105; C2 172 judged pairs",
               "limits": ["development experiments, not held-out", "no API or vector calls",
                          "PRF feedback uses only current facts text and unsupervised corpus term frequencies",
                          "query/gold/production source and defaults unchanged"], "runs": runs}
    output = DATA / "reports/contracts.prf_trials.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if output.exists():
        raise FileExistsError(output)
    output.write_text(text, encoding="utf-8")
    print(f"REPORT {output}")


if __name__ == "__main__":
    main()
