"""真实用户输入回归；固定检索通道重放，不调用远端模型。"""

import json
from pathlib import Path

import pytest

from casepath.adapters.conditions.semantic_condition_projector import SemanticConditionProjector
from casepath.adapters.questioning.key_condition_policy import KeyConditionQuestionPolicy
from casepath.adapters.retrieval.graph_navigator import MemoryGraphNavigator
from casepath.adapters.retrieval.hybrid_cypher import HybridCypherRuleRetriever
from casepath.adapters.retrieval.retrieval_catalog import load_canonical_catalog, scored_reference
from casepath.adapters.retrieval.rule_relevance import (
    RelevanceBatch,
    RelevanceItem,
    RelevantRuleRetriever,
)
from casepath.contracts import ComparisonBundle, ConditionStatus, QueryState, RetrievalBundle


@pytest.mark.parametrize("invalid", [False, True])
def test_relevance_filter_preserves_uncertain_and_rejects_invalid_ids(invalid):
    catalog = load_canonical_catalog(Path("data"))
    ids = ["rule.service_contract.termination_refund.v1", "rule.transport.passenger_missed_ride.v1"]

    class Retriever:
        def retrieve(self, state):
            return [
                scored_reference(key, 1.0, channels=[], components=[], reasons=[]) for key in ids
            ]

    class Model:
        def generate_structured(self, **kwargs):
            assert len(json.loads(kwargs["user_prompt"])["candidates"]) == 2
            return RelevanceBatch(
                items=[
                    RelevanceItem(
                        rule_id="invented" if invalid else ids[0],
                        relevance="UNCERTAIN",
                        reason="事实未全，仍保留",
                    ),
                    RelevanceItem(rule_id=ids[1], relevance="UNRELATED", reason="不是乘车交易"),
                ]
            )

    retriever = RelevantRuleRetriever(Retriever(), catalog, Model())
    state = QueryState(session_id="filter", initial_query="健身房退款")
    kept = [ref.object_id for ref in retriever.retrieve(state)]
    if invalid:
        # 虚构 rule_id 被丢弃；遗漏候选按 BACKGROUND 补回，UNRELATED 仍排除。
        assert ids[1] not in kept
        assert ids[0] in kept
    else:
        assert kept == [ids[0]]


def test_empty_retrieval_does_not_project_entire_catalog():
    catalog = load_canonical_catalog(Path("data"))
    state = SemanticConditionProjector(catalog).project(
        QueryState(session_id="empty", initial_query="咨询"), []
    )
    assert state.condition_states == []


def test_gym_closure_keeps_question_and_does_not_promote_graph_only_rule():
    catalog = load_canonical_catalog(Path("data"))
    service = "rule.service_contract.termination_refund.v1"
    transport = "rule.transport.passenger_missed_ride.v1"

    class Search:
        def search(self, *args, **kwargs):
            # 重放真实请求中的两个通道首位：向量服务退款、全文退票。
            return [
                scored_reference(transport, 26.0, channels=["bm25"], components=[], reasons=[]),
                scored_reference(service, 0.55, channels=["vector"], components=[], reasons=[]),
            ]

    graph = MemoryGraphNavigator(catalog)
    refs = HybridCypherRuleRetriever(graph, Search(), catalog=catalog).retrieve(
        QueryState(
            session_id="gym",
            initial_query="我在健身房充了5000元，店关门了，还有3000元没消费，老板不退款。",
        )
    )
    assert refs[0].object_id == service
    state = SemanticConditionProjector(catalog).project(
        QueryState(
            session_id="gym",
            initial_query="我在健身房充了5000元，店关门了，还有3000元没消费，老板不退款。",
        ),
        refs,
    )
    statuses = {item.condition_id: item.status for item in state.condition_states}
    assert statuses["cond.unperformed_balance"] == ConditionStatus.SATISFIED
    assert statuses["cond.performance_impossible"] == ConditionStatus.UNKNOWN
    question = KeyConditionQuestionPolicy(catalog).select(
        state, RetrievalBundle(rule_refs=refs), ComparisonBundle()
    )
    assert question is not None
    assert question.condition_id in catalog.rules[service].condition_ids
    assert "527" not in question.question
    assert "旅客" not in question.question


def test_single_rule_without_cases_still_clarifies_key_fact():
    catalog = load_canonical_catalog(Path("data"))
    refs = [
        scored_reference(
            "rule.service_contract.termination_refund.v1",
            1.0,
            channels=[],
            components=[],
            reasons=[],
        )
    ]
    state = SemanticConditionProjector(catalog).project(
        QueryState(
            session_id="one",
            initial_query="我在健身房充了5000元，店关门了，还有3000元没消费，老板不退款。",
        ),
        refs,
    )
    question = KeyConditionQuestionPolicy(catalog).select(
        state, RetrievalBundle(rule_refs=refs), ComparisonBundle()
    )
    assert question is not None
    # 新策略同级按规则排名、条件ID排序，不再绑定旧模拟的最高分条件。
    assert question.condition_id in {"cond.performance_impossible", "cond.contract_terminated"}
