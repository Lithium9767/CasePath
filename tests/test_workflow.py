from dataclasses import replace

import pytest

from casepath.bootstrap import build_demo_workflow
from casepath.contracts import DialogueTurn, QueryState, QuestionCandidate, SessionStatus
from casepath.workflow import CasePathWorkflow, WorkflowInvariantError


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
    assert snapshot.comparison_bundle is not None
    assert snapshot.trace.index("PROJECT_QUERY") < snapshot.trace.index("RETRIEVE_CASES")
    assert snapshot.trace.index("RETRIEVE_CASES") < snapshot.trace.index("COMPARE_CASES")


def test_case_retriever_receives_projected_condition_state():
    """P4案例检索必须看到同一轮已经完成的用户条件投影。"""
    base = build_demo_workflow()
    delegate = base.dependencies.case_retriever

    class ObservingCaseRetriever:
        def __init__(self):
            self.saw_projected_state = False

        def retrieve(self, state, rule_refs):
            self.saw_projected_state = any(
                item.condition_id == "cond.contract_exists"
                for item in state.condition_states
            )
            return delegate.retrieve(state, rule_refs)

    observer = ObservingCaseRetriever()
    workflow = CasePathWorkflow(replace(base.dependencies, case_retriever=observer))
    workflow.run(QueryState(session_id="ordered", initial_query="我在健身房办卡"))
    assert observer.saw_projected_state


def test_zero_question_budget_stops_without_resolving_unknown():
    """预算耗尽只是停止追问，不得把未知条件当成满足。"""
    workflow = CasePathWorkflow(build_demo_workflow().dependencies, max_question_turns=0)
    snapshot = workflow.run(QueryState(session_id="budget", initial_query="健身房关门"))
    assert snapshot.next_question is None
    assert "QUESTION_BUDGET_REACHED" in snapshot.trace
    assert "cond.performance_impossible" in snapshot.explanation_plan.unresolved_condition_ids


def test_demo_policy_filters_an_answered_unknown_condition():
    """回答“不知道”后条件仍为 UNKNOWN，但 Demo 策略不能重复选择它。"""
    workflow = build_demo_workflow()
    initial = workflow.run(QueryState(session_id="unknown", initial_query="健身房关门"))
    question = initial.next_question
    state = QueryState.model_validate(
        {
            **initial.query_state.model_dump(),
            "dialogue_history": [
                DialogueTurn(
                    turn_id=1,
                    question_id=question.question_id,
                    condition_id=question.condition_id,
                    question=question.question,
                    answer="不清楚",
                )
            ],
        }
    )
    result = workflow.run(state)
    assert result.next_question is None
    assert result.query_state.status == SessionStatus.READY_TO_EXPLAIN


def test_workflow_rejects_a_policy_that_selects_answered_condition():
    """防重是策略职责；工作流只校验不变量，不把策略错误伪装成正常停止。"""
    base = build_demo_workflow()
    initial = base.run(QueryState(session_id="bad-policy", initial_query="健身房关门"))
    question = initial.next_question
    state = QueryState.model_validate(
        {
            **initial.query_state.model_dump(),
            "dialogue_history": [
                DialogueTurn(
                    turn_id=1,
                    question_id=question.question_id,
                    condition_id=question.condition_id,
                    question=question.question,
                    answer="不清楚",
                )
            ],
        }
    )

    class RepeatingPolicy:
        def select(self, state, bundle, comparison):
            return QuestionCandidate(
                question_id=question.question_id,
                condition_id=question.condition_id,
                question=question.question,
                why_asked=question.why_asked,
                options=question.options,
                utility=question.utility,
            )

    workflow = CasePathWorkflow(replace(base.dependencies, question_policy=RepeatingPolicy()))
    with pytest.raises(WorkflowInvariantError, match="answered question"):
        workflow.run(state)
