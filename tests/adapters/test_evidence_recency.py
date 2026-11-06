"""同条件证据按 source_turn 越大越优先；仅最新一轮仍相反才 CONFLICTING。"""

from casepath.adapters.conditions.semantic_condition_projector import _aggregate
from casepath.contracts import ConditionEvidence, ConditionStatus


def test_newer_opposes_overrides_older_supports():
    state = _aggregate(
        "cond.payment_made",
        [
            ConditionEvidence(
                fact_id="f.old",
                relation="SUPPORTS",
                confidence=0.8,
                reason="旧",
                source_turn=0,
            ),
            ConditionEvidence(
                fact_id="f.new",
                relation="OPPOSES",
                confidence=0.7,
                reason="新",
                source_turn=2,
            ),
        ],
        reasons=["unit"],
    )
    assert state.status is ConditionStatus.NOT_SATISFIED
    assert any("按更新轮次裁决" in reason for reason in state.mapping_reasons)
    assert {item.fact_id for item in state.evidence} == {"f.old", "f.new"}


def test_newer_supports_overrides_older_opposes():
    state = _aggregate(
        "cond.payment_made",
        [
            ConditionEvidence(
                fact_id="f.old",
                relation="OPPOSES",
                confidence=0.8,
                reason="旧",
                source_turn=1,
            ),
            ConditionEvidence(
                fact_id="f.new",
                relation="SUPPORTS",
                confidence=0.7,
                reason="新",
                source_turn=3,
            ),
        ],
        reasons=["unit"],
    )
    assert state.status is ConditionStatus.SATISFIED


def test_same_turn_opposite_remains_conflicting():
    state = _aggregate(
        "cond.payment_made",
        [
            ConditionEvidence(
                fact_id="f.a",
                relation="SUPPORTS",
                confidence=0.8,
                reason="同轮支持",
                source_turn=1,
            ),
            ConditionEvidence(
                fact_id="f.b",
                relation="OPPOSES",
                confidence=0.8,
                reason="同轮反对",
                source_turn=1,
            ),
        ],
        reasons=["unit"],
    )
    assert state.status is ConditionStatus.CONFLICTING


def test_same_direction_keeps_satisfied_with_both_evidence():
    state = _aggregate(
        "cond.payment_made",
        [
            ConditionEvidence(
                fact_id="f.old",
                relation="SUPPORTS",
                confidence=0.6,
                reason="旧金额",
                source_turn=0,
            ),
            ConditionEvidence(
                fact_id="f.new",
                relation="SUPPORTS",
                confidence=0.9,
                reason="新金额",
                source_turn=2,
            ),
        ],
        reasons=["unit"],
    )
    assert state.status is ConditionStatus.SATISFIED
    assert state.confidence == 0.9
    assert len(state.evidence) == 2
