"""P4 回答解释器的演示基线。

负责人：P4。P4 可在保持 AnswerInterpreter 输入输出合同兼容的前提下替换本实现，
完成用户回答到 UserFact 和 QueryConditionState 的条件级语义映射。
P1 只负责调用、校验、合并和保存；当前实现不做法律语义判断。
"""

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
