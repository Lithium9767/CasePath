"""A relevance schema failure degrades instead of HTTP 500."""

from pathlib import Path

from casepath.adapters.retrieval.retrieval_catalog import load_canonical_catalog, scored_reference
from casepath.adapters.retrieval.rule_relevance import RelevantRuleRetriever
from casepath.application.errors import InvalidComponentOutput
from casepath.contracts import QueryState


def test_relevance_schema_failure_degrades_to_default_scope():
    catalog = load_canonical_catalog(Path("data"))
    ids = [
        "rule.service_contract.termination_refund.v1",
        "rule.transport.passenger_missed_ride.v1",
    ]

    class Retriever:
        def retrieve(self, state):
            return [
                scored_reference(key, 1.0, channels=[], components=[], reasons=[]) for key in ids
            ]

    class BadModel:
        def generate_structured(self, **kwargs):
            raise InvalidComponentOutput("structured schema boom")

    retriever = RelevantRuleRetriever(Retriever(), catalog, BadModel())
    state = QueryState(session_id="s-deg", initial_query="健身房退款")
    kept = retriever.retrieve(state)
    # Degraded: all candidates remain active-scoped; must not raise.
    assert {ref.object_id for ref in kept} == set(ids)
