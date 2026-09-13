"""Evaluate the built-in BM25 case retriever against relation-level LexRel qrels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from casepath.adapters.retrieval.retrieval_catalog import MemoryHybridSearch, load_canonical_catalog


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument(
        "--qrels",
        type=Path,
        default=Path("data/evaluation/case_retrieval_qrels.jsonl"),
    )
    parser.add_argument("--k", type=int, nargs="+", default=[5, 10, 20])
    args = parser.parse_args()
    if any(k <= 0 for k in args.k):
        parser.error("every k must be positive")

    catalog = load_canonical_catalog(args.data_root)
    search = MemoryHybridSearch(catalog)
    qrels = [
        json.loads(line)
        for line in args.qrels.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    results: dict[str, float] = {}
    for k in args.k:
        recalls: list[float] = []
        for query in qrels:
            hits = search.search(
                query["query_text"],
                target="case",
                context=catalog.context(),
                top_k=k,
            )
            retrieved = {item.object_id for item in hits}
            relevant = set(query["relevant_case_ids"])
            recalls.append(len(retrieved & relevant) / len(relevant))
        results[f"macro_recall@{k}"] = sum(recalls) / len(recalls)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
