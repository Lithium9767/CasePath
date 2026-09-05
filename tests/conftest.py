"""会话测试使用固定 P4 替身，测试结果不代表真实法律语义识别能力。"""

import pytest

from casepath.bootstrap import build_demo_session_service
from casepath.contracts import (
    AnswerInterpretation,
    AnswerRequest,
    ConditionEvidence,
    ConditionStatus,
    CreateSessionRequest,
    QueryConditionState,
    UserFact,
)


class FixedAnswerInterpreter:
    """只在测试中指定状态，用来证明 P1 能正确调用和合并 P4 输出。"""

    def __init__(self, status=ConditionStatus.SATISFIED):
        self.status = status
        self.calls = 0

    def interpret(self, state, pending_question, answer_request):
        self.calls += 1
        turn_id = max((turn.turn_id for turn in state.dialogue_history), default=0) + 1
        fact = UserFact(
            fact_id=f"fact.test.{turn_id}", text=answer_request.answer, source_turn=turn_id
        )
        return AnswerInterpretation(
            new_facts=[fact],
            condition_updates=[
                QueryConditionState(
                    condition_id=pending_question.condition_id,
                    status=self.status,
                    supporting_fact_ids=[fact.fact_id],
                    confidence=0.99,
                    evidence=[
                        ConditionEvidence(
                            fact_id=fact.fact_id,
                            relation="SUPPORTS",
                            confidence=0.99,
                            reason="固定测试映射",
                        )
                    ],
                    mapping_reasons=["固定测试解释器输出"],
                    last_updated_turn=turn_id,
                )
            ],
        )


@pytest.fixture
def service():
    result = build_demo_session_service()
    result.answer_interpreter = FixedAnswerInterpreter()
    return result


@pytest.fixture
def initial(service):
    return service.create_session(
        CreateSessionRequest(query="我在健身房充了5000元，店关门了，还有余额。")
    )


@pytest.fixture
def answer(initial):
    question = initial.next_question
    return AnswerRequest(
        question_id=question.question_id,
        condition_id=question.condition_id,
        answer="所有门店都已经永久关闭了",
        selected_option="永久停止经营",
    )
