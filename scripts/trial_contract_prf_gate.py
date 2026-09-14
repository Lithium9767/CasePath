"""Inspect sentence-count PRF gating on actual projected runtime queries."""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from trial_contract_prf import PRESETS, c2_observation, constructor, read_rows

from casepath.adapters.retrieval.retrieval_catalog import MemoryHybridSearch, load_canonical_catalog
from casepath.evaluation import ac_retrieval as evaluation
from casepath.evaluation.relation_coverage import (
    relation_queries,
    scope_catalog,
    select_query_scope,
    source_labels,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
FEEDBACK = constructor(PRESETS[2])
EVENTS = []


def gate(text):
    # Query content only: no ID, domain, qrel, title or label enters the gate.
    parts = {part.strip() for part in re.split(r"[\u3002\uff01\uff1f?!\uff1b;]", text)}
    qualifying = sorted(part for part in parts if len(part) >= 8)
    return len(qualifying) >= 2, qualifying


class AdaptiveFeedback(FEEDBACK):
    def search(self, text, *, target, context, top_k):
        enabled, qualifying = gate(text)
        if target == "case":
            EVENTS.append({"text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                           "text": text, "enabled": enabled, "qualifying_parts": qualifying})
        if target == "case" and enabled:
            return super().search(text, target=target, context=context, top_k=top_k)
        return MemoryHybridSearch.search(self, text, target=target, context=context, top_k=top_k)


def run(catalog, queries):
    EVENTS.clear()
    result = evaluation.evaluate_queries(catalog, queries)
    if len(EVENTS) != len(queries) * 2:
        raise ValueError("unexpected number of runtime case searches")
    annotated = []
    for offset, query in enumerate(queries):
        first, second = EVENTS[2*offset:2*offset+2]
        if first != second:
            raise ValueError("retriever and baseline queries differ")
        annotated.append({"query_id": query.query_id, **first})
    return result, annotated


def main():
    source_paths = list((ROOT / "src/casepath/adapters/retrieval").glob("*.py"))
    source_paths += list((ROOT / "src/casepath/evaluation").glob("*.py"))
    source_paths += [ROOT / "scripts/trial_contract_prf.py", Path(__file__)]
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}
    catalog = load_canonical_catalog(DATA)
    facts = evaluation.facts_only_catalog(DATA, catalog)
    queries = evaluation.load_queries(DATA / "evaluation/ac_queries.jsonl", catalog)
    labels = source_labels(DATA)
    topics, relations = relation_queries(DATA, queries, labels)
    contract, _ = select_query_scope(topics, relations, contract_only=True)
    scoped = scope_catalog(facts, labels)
    judgments = defaultdict(dict)
    for part in ("part1", "part2", "u05_extra"):
        for row in read_rows(DATA / f"evaluation/c2_judgments.{part}.jsonl"):
            if row["case_id"] in judgments[row["query_id"]]:
                raise ValueError("duplicate judgment")
            judgments[row["query_id"]][row["case_id"]] = row["grade"]
    c2 = [evaluation.EvaluationQuery(
        query_id=row["query_id"], module="C", domain=row["domain"], query_text=row["query_text"],
        qrels={}, judgment_basis="ranking_only_pending_additional_judgments", source_refs=(),
    ) for row in read_rows(DATA / "evaluation/c2_queries.jsonl")]
    original = evaluation.MemoryHybridSearch
    try:
        evaluation.MemoryHybridSearch = AdaptiveFeedback
        mixed, mixed_gates = run(facts, contract)
        c1, c1_gates = run(scoped, contract)
        c2_run, c2_gates = run(facts, c2)
    finally:
        evaluation.MemoryHybridSearch = original
    after = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}
    if hashes != after:
        raise RuntimeError("retrieval source changed during gate trial")
    prior = json.loads((DATA / "reports/contracts.prf_trials.json").read_text(encoding="utf-8"))
    # Ensure cached baseline reflects identical retrieval/evaluation source.
    for path, expected in prior["source_sha256"].items():
        if path in hashes and expected != hashes[path]:
            raise ValueError(f"cached baseline source changed: {path}")
    baseline = prior["runs"][0]
    payload = {"source_sha256": hashes, "gate": "at least two distinct stripped segments of >=8 characters; split only on Chinese/ASCII sentence punctuation, not newlines",
               "preset": PRESETS[2], "mixed156": mixed, "c1_labeled105": c1,
               "c2_observed172": c2_observation(c2_run, judgments),
               "gates": {"mixed156": mixed_gates, "c1_labeled105": c1_gates, "c2": c2_gates},
               "baseline_cached_report_sha256": hashlib.sha256((DATA / "reports/contracts.prf_trials.json").read_bytes()).hexdigest()}
    for key in ("mixed156", "c1_labeled105"):
        same = [row["rankings"] == base["rankings"] for row, base in zip(payload[key]["queries"], baseline[key]["queries"])]
        print(key, "enabled", sum(row["enabled"] for row in payload["gates"][key]), "identical_queries", sum(same), "of", len(same))
        print(json.dumps(payload[key]["summary"]["c.fused"]["macro_at_k"]))
    print("C2_GATES", json.dumps([{k: row[k] for k in ("query_id", "enabled")} for row in c2_gates]))
    print("C2_OBSERVED", json.dumps(payload["c2_observed172"]["summary"]))
    output = DATA / "reports/contracts.prf_gate_trial.json"
    if output.exists():
        raise FileExistsError(output)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("REPORT", output)


if __name__ == "__main__":
    main()
