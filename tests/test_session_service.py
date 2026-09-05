"""跨轮次服务测试：语义由测试替身给定，P1 只处理状态、证据和调用顺序。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest

from casepath.application.errors import (
    AnswerInterpreterUnavailable,
    InvalidAnswer,
    InvalidComponentOutput,
    SessionConflict,
    SessionNotFound,
)
from casepath.bootstrap import build_demo_session_service
from casepath.contracts import (
    AnswerInterpretation,
    AnswerRequest,
    ConditionStatus,
    CreateSessionRequest,
    QueryConditionState,
    UserFact,
)


def test_create_read_and_independent_sessions(service, initial):
    assert service.get_session(initial.query_state.session_id) == initial
    other = service.create_session(CreateSessionRequest(query=initial.query_state.initial_query))
    assert other.query_state.session_id != initial.query_state.session_id
    with pytest.raises(SessionNotFound):
        service.get_session("missing")


def test_answer_updates_facts_conditions_and_retrieves_again(service, initial, answer):
    seen = []
    retriever = service.workflow.dependencies.case_retriever

    class RecordingRetriever:
        def retrieve(self, state, refs):
            seen.append(state.model_copy(deep=True))
            return retriever.retrieve(state, refs)

    service.workflow.dependencies = replace(
        service.workflow.dependencies, case_retriever=RecordingRetriever()
    )
    snapshot = service.submit_answer(initial.query_state.session_id, answer)
    state = snapshot.query_state
    condition = next(
        item for item in state.condition_states if item.condition_id == answer.condition_id
    )
    assert condition.status == ConditionStatus.SATISFIED
    facts = {item.fact_id: item for item in state.user_facts}
    # Demo初始投影可保留初始问题片段；本轮回答仍只产生一条source_turn=1事实。
    assert sum(item.source_turn == 1 for item in facts.values()) == 1
    assert all(facts[key].text == answer.answer for key in condition.supporting_fact_ids)
    assert condition.evidence[0].fact_id in facts
    assert condition.confidence == 0.99
    assert state.dialogue_history[0].question == initial.next_question.question
    assert state.dialogue_history[0].answer == answer.answer
    assert all(
        item.text == answer.answer for item in state.user_facts if item.source_turn == 1
    )
    assert seen[0].dialogue_history == state.dialogue_history
    assert seen[0].condition_states == state.condition_states
    assert snapshot.next_question is None
    assert service.repository.get(state.session_id).revision == 1


def test_replay_is_exact_and_changed_answer_is_conflict(service, initial, answer):
    session_id = initial.query_state.session_id
    first = service.submit_answer(session_id, answer)
    second = service.submit_answer(session_id, answer)
    assert first == second
    assert service.answer_interpreter.calls == 1
    changed = answer.model_copy(update={"answer": "改成另一段回答"})
    with pytest.raises(SessionConflict):
        service.submit_answer(session_id, changed)
    assert service.repository.get(session_id).revision == 1


@pytest.mark.parametrize(
    "change",
    [
        {"question_id": "question.wrong"},
        {"condition_id": "cond.wrong"},
    ],
)
def test_mismatched_question_preserves_state(service, initial, answer, change):
    with pytest.raises(SessionConflict):
        service.submit_answer(initial.query_state.session_id, answer.model_copy(update=change))
    assert service.get_session(initial.query_state.session_id) == initial


@pytest.mark.parametrize("change", [{"answer": " \n "}, {"selected_option": "不存在的选项"}])
def test_invalid_answer_preserves_state(service, initial, answer, change):
    with pytest.raises(InvalidAnswer):
        service.submit_answer(initial.query_state.session_id, answer.model_copy(update=change))
    assert service.get_session(initial.query_state.session_id) == initial


def test_unavailable_interpreter_preserves_state(service, initial, answer):
    service.answer_interpreter = None
    with pytest.raises(AnswerInterpreterUnavailable):
        service.submit_answer(initial.query_state.session_id, answer)
    assert service.get_session(initial.query_state.session_id) == initial


def test_exception_or_mutation_in_interpreter_cannot_corrupt_state(service, initial, answer):
    class FailingInterpreter:
        def interpret(self, state, question, request):
            state.initial_query = "意外篡改"
            raise RuntimeError("secret-internal-error")

    service.answer_interpreter = FailingInterpreter()
    with pytest.raises(AnswerInterpreterUnavailable):
        service.submit_answer(initial.query_state.session_id, answer)
    assert service.get_session(initial.query_state.session_id) == initial


@pytest.mark.parametrize(
    "kind", ["invented_quote", "unknown_condition", "missing_evidence", "turn"]
)
def test_invalid_interpretation_is_not_saved(service, initial, answer, kind):
    class BadInterpreter:
        def interpret(self, state, question, request):
            fact = UserFact(
                fact_id="fact.bad",
                text="凭空编造" if kind == "invented_quote" else request.answer,
                source_turn=0 if kind == "turn" else 1,
            )
            update = QueryConditionState(
                condition_id="cond.unknown"
                if kind == "unknown_condition"
                else question.condition_id,
                status=ConditionStatus.SATISFIED,
                supporting_fact_ids=[] if kind == "missing_evidence" else [fact.fact_id],
                last_updated_turn=1,
            )
            return AnswerInterpretation(new_facts=[fact], condition_updates=[update])

    service.answer_interpreter = BadInterpreter()
    with pytest.raises(InvalidComponentOutput):
        service.submit_answer(initial.query_state.session_id, answer)
    assert service.get_session(initial.query_state.session_id) == initial


def test_workflow_failure_rolls_back_entire_answer(service, initial, answer):
    class FailingWorkflow:
        def run(self, state):
            raise RuntimeError("检索失败")

    service.workflow = FailingWorkflow()
    with pytest.raises(RuntimeError):
        service.submit_answer(initial.query_state.session_id, answer)
    record = service.repository.get(initial.query_state.session_id)
    assert record.latest_snapshot == initial
    assert record.revision == 0
    assert not record.answer_receipts


def test_demo_unknown_answer_stays_unknown_and_is_not_reasked():
    service = build_demo_session_service()
    initial = service.create_session(CreateSessionRequest(query="健身房关门了，还有余额"))
    question = initial.next_question
    snapshot = service.submit_answer(
        initial.query_state.session_id,
        AnswerRequest(
            question_id=question.question_id,
            condition_id=question.condition_id,
            answer="不记得了",
            selected_option="不清楚",
        ),
    )
    assert snapshot.next_question is None
    assert question.condition_id in snapshot.explanation_plan.unresolved_condition_ids
    assert snapshot.query_state.dialogue_history[0].answer == "不记得了"
    # 保留初始问题中的可定位片段，但保守Demo解释器不从“不记得”中制造新事实。
    assert all(item.source_turn == 0 for item in snapshot.query_state.user_facts)
    assert "VERIFY_CITATIONS" not in snapshot.trace
    assert "CITATION_VERIFICATION_NOT_CONFIGURED" not in snapshot.trace
    assert not any(item.verified for item in snapshot.explanation_plan.citations)


@pytest.mark.parametrize("same_answer", [True, False])
def test_concurrent_answers_commit_once(service, initial, answer, same_answer):
    barrier = Barrier(2)
    interpreter = service.answer_interpreter

    class BlockingInterpreter:
        def interpret(self, state, question, request):
            result = interpreter.interpret(state, question, request)
            barrier.wait(timeout=5)
            return result

    service.answer_interpreter = BlockingInterpreter()
    other = answer if same_answer else answer.model_copy(update={"answer": "另一个回答"})

    def submit(request):
        try:
            return service.submit_answer(initial.query_state.session_id, request)
        except SessionConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, [answer, other]))
    assert sum(item is not None for item in results) == (2 if same_answer else 1)
    if same_answer:
        assert results[0] == results[1]
    record = service.repository.get(initial.query_state.session_id)
    assert record.revision == 1
    assert len(record.latest_snapshot.query_state.dialogue_history) == 1


def test_three_turns_budget_and_old_replay_never_roll_back_latest(service):
    """合成的多问题策略用于验证基础设施，不表示真实追问的价值排序。"""
    from casepath.contracts import QuestionCandidate

    class SequentialPolicy:
        def select(self, state, bundle, comparison):
            asked = {turn.condition_id for turn in state.dialogue_history}
            condition = next(
                item for item in state.condition_states if item.condition_id not in asked
            )
            return QuestionCandidate(
                question_id=f"question.test.{len(asked) + 1}",
                condition_id=condition.condition_id,
                question="请补充你知道的信息",
                why_asked="仅测试多轮交互",
                options=["知道", "不知道"],
                utility=0.5,
            )

    service.workflow.dependencies = replace(
        service.workflow.dependencies, question_policy=SequentialPolicy()
    )
    snapshot = service.create_session(CreateSessionRequest(query="健身房关门了，还有余额"))
    session_id = snapshot.query_state.session_id
    first_request = None
    first_result = None
    for turn_id in range(1, 4):
        question = snapshot.next_question
        request = AnswerRequest(
            question_id=question.question_id,
            condition_id=question.condition_id,
            answer=f"第 {turn_id} 次补充信息",
        )
        snapshot = service.submit_answer(session_id, request)
        assert len(snapshot.query_state.dialogue_history) == turn_id
        if turn_id == 1:
            first_request, first_result = request, snapshot
    assert snapshot.next_question is None
    assert "QUESTION_BUDGET_REACHED" in snapshot.trace
    assert service.submit_answer(session_id, first_request) == first_result
    assert service.get_session(session_id) == snapshot
    assert service.repository.get(session_id).revision == 3


def test_workflow_cannot_drop_answer_evidence(service, initial, answer):
    workflow = service.workflow

    class LosingWorkflow:
        def run(self, state):
            result = workflow.run(state)
            # 同时清掉条件引用，使基础合同仍然能通过；应用层仍应拒绝证据丢失。
            result.query_state.user_facts.clear()
            for condition in result.query_state.condition_states:
                condition.supporting_fact_ids.clear()
            return result

    service.workflow = LosingWorkflow()
    with pytest.raises(InvalidComponentOutput):
        service.submit_answer(initial.query_state.session_id, answer)
    assert service.get_session(initial.query_state.session_id) == initial
