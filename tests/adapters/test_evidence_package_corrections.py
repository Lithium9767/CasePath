"""解释证据包：已纠正事实必须与有效事实隔离。"""

from typing import ClassVar

from casepath.adapters.explanation.grounded_explanation import evidence_package
from casepath.adapters.runtime.request_context import p4_request_context
from casepath.application.runtime_meta import CorrectionLink, SessionRuntimeMeta
from casepath.contracts import (
    ComparisonBundle,
    ConditionEvidence,
    ConditionStatus,
    QueryConditionState,
    QueryState,
    RetrievalBundle,
    UserFact,
)


class _Catalog:
    release_id = "test-release"
    rules: ClassVar[dict] = {}
    conditions: ClassVar[dict] = {
        "cond.payment_made": type("C", (), {"label": "已付款", "predicate": "已付款"})(),
    }
    rule_records: ClassVar[dict] = {}
    case_records: ClassVar[dict] = {}


def test_corrected_fact_is_marked_audit_and_dropped_from_condition_evidence():
    meta = SessionRuntimeMeta(
        corrections=[
            CorrectionLink(
                target_fact_id="fact.old",
                replacement_fact_id="fact.new",
                condition_id="cond.payment_made",
                reason="明确纠正金额",
            )
        ]
    )
    state = QueryState(
        session_id="s-ev",
        initial_query="交了5000元",
        user_facts=[
            UserFact(fact_id="fact.old", text="交了5000元", source_turn=0),
            UserFact(fact_id="fact.new", text="金额说错了，是6000元。", source_turn=1),
        ],
        dialogue_history=[
            __import__("casepath.contracts", fromlist=["DialogueTurn"]).DialogueTurn(
                turn_id=1,
                question_id="q1",
                condition_id="cond.payment_made",
                question="金额？",
                answer="金额说错了，是6000元。",
            )
        ],
        condition_states=[
            QueryConditionState(
                condition_id="cond.payment_made",
                status=ConditionStatus.SATISFIED,
                supporting_fact_ids=["fact.new"],
                evidence=[
                    ConditionEvidence(
                        fact_id="fact.new",
                        relation="SUPPORTS",
                        confidence=0.8,
                        reason="纠正后",
                    )
                ],
                mapping_reasons=["纠正后"],
            )
        ],
    )
    # 人为塞入旧证据，确认证据包构造会按纠正链隔离
    state.condition_states[0].evidence.append(
        ConditionEvidence(
            fact_id="fact.old",
            relation="SUPPORTS",
            confidence=0.8,
            reason="旧金额",
        )
    )
    state.condition_states[0].supporting_fact_ids = ["fact.new", "fact.old"]

    bundle = RetrievalBundle(
        rule_refs=[
            __import__("casepath.contracts", fromlist=["ScoredReference"]).ScoredReference(
                object_id="rule.x",
                score=1.0,
                reasons=["t"],
                retrieval_channels=["bm25"],
                score_components=[
                    __import__("casepath.contracts", fromlist=["ScoreComponent"]).ScoreComponent(
                        name="rrf", value=1.0, explanation="t"
                    )
                ],
            )
        ]
    )
    # 让 relevant 包含 payment：需要 catalog.rules
    catalog = _Catalog()
    catalog.rules = {
        "rule.x": type("R", (), {"condition_ids": ("cond.payment_made",)})()
    }

    with p4_request_context(meta):
        package = evidence_package(catalog, state, bundle, ComparisonBundle())

    by_id = {item["fact_id"]: item for item in package["facts"]}
    assert by_id["fact.old"]["evidence_role"] == "partially_superseded"
    assert by_id["fact.new"]["evidence_role"] == "effective"
    assert "audit" in " ".join(package["limitations"]).lower() or "纠正" in "".join(
        package["limitations"]
    )
    payment = next(item for item in package["conditions"] if item["condition_id"] == "cond.payment_made")
    evidence_ids = {item["fact_id"] for item in payment["evidence"]}
    assert "fact.old" not in evidence_ids
    assert "fact.new" in evidence_ids
