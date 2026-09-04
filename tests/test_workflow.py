from casepath.bootstrap import build_demo_workflow
from casepath.contracts import ConditionStatus, QueryState, SessionStatus


def test_demo_workflow_asks_high_value_question():
    workflow = build_demo_workflow()
    snapshot = workflow.run(
        QueryState(
            session_id="test-session",
            initial_query="我在健身房充了5000元，店关门了，还有余额。",
        )
    )

    assert snapshot.query_state.status == SessionStatus.NEEDS_CLARIFICATION
    assert snapshot.next_question is not None
    assert snapshot.next_question.condition_id == "cond.performance_impossible"
    assert snapshot.retrieval_bundle.support_case_refs


def test_answer_updates_condition_and_stops_reasking_it():
    workflow = build_demo_workflow()
    initial = workflow.run(
        QueryState(
            session_id="test-session",
            initial_query="我在健身房充了5000元，店关门了，还有余额。",
        )
    )
    updated = workflow.apply_answer(
        initial.query_state,
        condition_id="cond.performance_impossible",
        answer="所有门店都永久关闭了",
        status=ConditionStatus.SATISFIED,
    )

    assert updated.next_question is None
    assert updated.query_state.status == SessionStatus.READY_TO_EXPLAIN
    assert "支持进一步分析" in updated.explanation_plan.main_explanation
