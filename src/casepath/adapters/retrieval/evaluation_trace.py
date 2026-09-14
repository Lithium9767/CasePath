"""Opt-in, request-local rankings for offline A/C retrieval evaluation.

This observer does not participate in scoring or expose a public API contract.
Only stage names, object IDs and optional scores are stored: never query text,
case facts, evidence quotes or model prompts. Use one capture per evaluation
query. Repeated stages within a capture replace the previous snapshot.

``c.fused`` is the full RRF ranking before role-based pool selection; ``c.pool``
is the actual role-diversified pool, not another relevance-sorted list. Role
lists must not be concatenated to reconstruct the retrieval ranking.
``c.undetermined`` stores a set in stable ID order, not a relevance ranking.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True)
class RankingSnapshot:
    stage: str
    object_ids: tuple[str, ...]
    # An empty tuple means this stage only records order, not scores.
    scores: tuple[float, ...] = ()


@dataclass
class RetrievalRankingCapture:
    """Successful captures remain readable after their context has exited."""

    _rankings: dict[str, RankingSnapshot] = field(default_factory=dict, repr=False)

    @property
    def rankings(self) -> Mapping[str, RankingSnapshot]:
        return MappingProxyType(self._rankings)

    def ids(self, stage: str) -> tuple[str, ...]:
        """Return an observed ranking; raise KeyError if the stage never ran.

        An observed empty ranking is ``()``. Keeping that distinct from an
        unobserved stage prevents failed retrievals from being scored as empty
        successful results.
        """

        return self._rankings[stage].object_ids


_CAPTURE: ContextVar[RetrievalRankingCapture | None] = ContextVar(
    "casepath_retrieval_evaluation_capture", default=None
)


@contextmanager
def capture_retrieval_rankings() -> Iterator[RetrievalRankingCapture]:
    """Capture only this scope; nested captures are isolated from their parent.

    An exception leaving the scope discards its partial results. In all cases
    the previous observer is restored, without touching retrieval context,
    RuleScope or RoundEvidence. No trace is retained in a retriever instance.
    """

    capture = RetrievalRankingCapture()
    token = _CAPTURE.set(capture)
    try:
        yield capture
    except BaseException:
        capture._rankings.clear()
        raise
    finally:
        _CAPTURE.reset(token)


def record_retrieval_ranking(
    stage: str,
    object_ids: Iterable[str],
    *,
    scores: Iterable[float] | None = None,
) -> None:
    """Copy an already computed order; never sort, truncate or change inputs.

    With no observer, neither iterable is consumed. Retrieval callers should
    therefore pass lazy generators for IDs/scores not already materialized.
    """

    capture = _CAPTURE.get()
    if capture is None:
        return
    capture._rankings[stage] = RankingSnapshot(
        stage=stage,
        object_ids=tuple(object_ids),
        scores=tuple(scores) if scores is not None else (),
    )
