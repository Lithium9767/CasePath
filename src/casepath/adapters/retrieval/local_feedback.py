"""Bounded, unsupervised lexical feedback for multi-sentence fact narratives.

Search hints are derived from the current indexed text only. They never become
user facts, condition assessments, court findings, or relevance judgments.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from casepath.adapters.retrieval.retrieval_catalog import BM25Index, tokenize

_SENTENCE_BOUNDARY = re.compile(r"[。！？?!；;]")


def is_fact_narrative(text: str) -> bool:
    """A syntactic gate, not a domain/intent classifier or a legal assertion."""
    segments = {
        part.strip() for part in _SENTENCE_BOUNDARY.split(text) if len(part.strip()) >= 8
    }
    return len(segments) >= 2


@dataclass(frozen=True)
class FeedbackResult:
    hits: tuple[tuple[str, float], ...]
    terms: tuple[str, ...] = ()
    seed_ids: tuple[str, ...] = ()


class LocalFeedbackIndex:
    """Top5 -> at most 20 source-text bigrams -> normalized lexical score + 0.2 hint.

    Fixed bounded development configuration. Short topic queries stay exactly
    unchanged. Single-document names and ubiquitous boilerplate are excluded by
    corpus document frequency, not by a benchmark-specific legal vocabulary.
    """

    def __init__(self, index: BM25Index) -> None:
        self.index = index

    def search(self, text: str, top_k: int) -> FeedbackResult:
        # Use the original index's input validation even when the gate is off.
        original = self.index.search(text, top_k)
        if not original or not is_fact_narrative(text):
            return FeedbackResult(tuple(original))
        index = self.index
        base = index.search(text, index.n)
        query_terms = set(tokenize(text))
        support: dict[str, float] = defaultdict(float)
        seeds = base[:5]
        for oid, doc_score in seeds:
            frequencies = Counter(index.tokens[oid])
            length = len(index.tokens[oid]) or 1
            for term, frequency in frequencies.items():
                df = len(index._postings[term])
                if (len(term) == 2 and all("\u4e00" <= char <= "\u9fff" for char in term)
                        and term not in query_terms and 2 <= df <= 0.35 * index.n):
                    support[term] += (
                        doc_score / base[0][1] * frequency / length * index.idf[term]
                    )
        terms = sorted(support, key=lambda term: (-support[term], term))[:20]
        if not terms:
            return FeedbackResult(tuple(original))
        expansion = index.search(" ".join(terms), index.n)
        if not expansion:
            return FeedbackResult(tuple(original))
        scores = {oid: value / base[0][1] for oid, value in base}
        for oid, value in expansion:
            scores[oid] = scores.get(oid, 0.0) + 0.2 * value / expansion[0][1]
        ranked = sorted(scores.items(), key=lambda row: (-row[1], row[0]))[:top_k]
        return FeedbackResult(tuple(ranked), tuple(terms), tuple(oid for oid, _ in seeds))
