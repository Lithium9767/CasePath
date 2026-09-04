from __future__ import annotations

from typing import Protocol

from casepath.contracts import (
    ExplanationPlan,
    QueryState,
    QuestionCandidate,
    RetrievalBundle,
    ScoredReference,
)


class RuleRetriever(Protocol):
    def retrieve(self, state: QueryState) -> list[ScoredReference]: ...


class CaseRetriever(Protocol):
    def retrieve(self, state: QueryState, rule_refs: list[ScoredReference]) -> RetrievalBundle: ...


class ConditionProjector(Protocol):
    def project(self, state: QueryState, bundle: RetrievalBundle) -> QueryState: ...


class QuestionPolicy(Protocol):
    def select(self, state: QueryState, bundle: RetrievalBundle) -> QuestionCandidate | None: ...


class ExplanationPlanner(Protocol):
    def build(self, state: QueryState, bundle: RetrievalBundle) -> ExplanationPlan: ...
