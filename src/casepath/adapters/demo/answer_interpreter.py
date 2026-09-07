"""离线演示用的回答解释器：只保留原文，不做条件语义映射。"""

from casepath.contracts import (
    AnswerInterpretation,
    AnswerRequest,
    QueryState,
    QuestionCandidate,
)


class DemoAnswerInterpreter:
    def interpret(
        self,
        state: QueryState,
        pending_question: QuestionCandidate,
        answer_request: AnswerRequest,
    ) -> AnswerInterpretation:
        # 不凭选项或关键词推断法律条件。SessionService 会把完整回答保存到对话历史。
        # UNKNOWN 保持 UNKNOWN，已问条件由 QuestionPolicy 过滤。
        return AnswerInterpretation()
