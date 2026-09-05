"""P1 维护的跨轮次会话服务。

本文件负责会话状态、回答回执、版本校验和工作流重跑；不计算法律条件是否成立。
P4 的回答理解只能通过 AnswerInterpreter 返回合同化结果，再由本服务校验和合并。
"""

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError

from casepath.contracts import (
    AnswerInterpretation,
    AnswerRequest,
    ConditionStatus,
    CreateSessionRequest,
    DialogueTurn,
    QueryState,
    WorkflowSnapshot,
)
from casepath.ports import AnswerInterpreter
from casepath.ports.session_repository import SessionRepository
from casepath.workflow import CasePathWorkflow, WorkflowInvariantError

from .errors import (
    AnswerInterpreterUnavailable,
    InvalidAnswer,
    InvalidComponentOutput,
    SessionConflict,
    SessionNotFound,
)
from .models import AnswerReceipt, SessionRecord


class SessionService:
    def __init__(
        self,
        repository: SessionRepository,
        workflow: CasePathWorkflow,
        answer_interpreter: AnswerInterpreter | None,
    ) -> None:
        self.repository = repository
        self.workflow = workflow
        self.answer_interpreter = answer_interpreter

    def create_session(self, request: CreateSessionRequest) -> WorkflowSnapshot:
        """创建正式会话；请求合同由调用边界构造并校验一次。"""
        state = QueryState(session_id=str(uuid4()), initial_query=request.query)
        snapshot = self._run(state)
        self.repository.create(SessionRecord(session_id=state.session_id, latest_snapshot=snapshot))
        return snapshot

    def _get_record(self, session_id: str) -> SessionRecord:
        record = self.repository.get(session_id)
        if record is None:
            raise SessionNotFound(session_id)
        return record

    def get_session(self, session_id: str) -> WorkflowSnapshot:
        return self._get_record(session_id).latest_snapshot

    @staticmethod
    def _replay(record: SessionRecord, request: AnswerRequest) -> WorkflowSnapshot | None:
        # question_id 在同一会话内唯一，因此天然可作为一次回答的幂等标识。
        receipt = record.answer_receipts.get(request.question_id)
        if receipt is None:
            return None
        if receipt.request != request:
            raise SessionConflict("该问题已回答，不能用不同内容覆盖原回答")
        return receipt.snapshot.model_copy(deep=True)

    def submit_answer(self, session_id: str, request: AnswerRequest) -> WorkflowSnapshot:
        # 请求模型在 API 或调用者边界校验；这里处理与当前会话有关的业务约束。
        if not request.answer.strip():
            raise InvalidAnswer("回答不能只有空白字符")
        record = self._get_record(session_id)
        replay = self._replay(record, request)
        if replay is not None:
            return replay

        pending = record.latest_snapshot.next_question
        if pending is None:
            raise SessionConflict("当前会话没有待回答问题")
        if (pending.question_id, pending.condition_id) != (
            request.question_id,
            request.condition_id,
        ):
            raise SessionConflict("回答与当前待回答问题不匹配，请刷新会话")
        if request.selected_option is not None and request.selected_option not in pending.options:
            raise InvalidAnswer("所选选项不属于当前问题")
        if self.answer_interpreter is None:
            raise AnswerInterpreterUnavailable("尚未接入 P4 回答解析能力")

        state = record.latest_snapshot.query_state
        try:
            # 把副本交给算法，防止其意外修改基线；问题原文始终取自服务端。
            interpretation = self.answer_interpreter.interpret(
                state.model_copy(deep=True),
                pending.model_copy(deep=True),
                request.model_copy(deep=True),
            )
        except AnswerInterpreterUnavailable:
            raise
        except Exception as exc:
            # 不把模型错误、内部 URL 或用户原文暴露给 HTTP 客户端。
            raise AnswerInterpreterUnavailable("回答解析失败，原会话未修改") from exc

        updated = self._merge_answer(state, pending.question, request, interpretation)
        snapshot = self._run(updated)
        # 这两个步骤在本方法中确实执行过；旧的单轮工作流不再冒充已解析回答。
        snapshot.trace = ["INTERPRET_ANSWER", "RECORD_ANSWER", *snapshot.trace]
        receipts = {
            **record.answer_receipts,
            request.question_id: AnswerReceipt(request=request, snapshot=snapshot),
        }
        candidate = SessionRecord(
            session_id=session_id,
            revision=record.revision + 1,
            latest_snapshot=snapshot,
            updated_at=datetime.now(UTC),
            answer_receipts=receipts,
        )
        try:
            self.repository.save(candidate, expected_revision=record.revision)
        except SessionConflict:
            # 两个相同请求并发完成时，后提交者重放胜出者的结果，不新增轮次。
            replay = self._replay(self._get_record(session_id), request)
            if replay is not None:
                return replay
            raise
        return snapshot

    @staticmethod
    def _merge_answer(
        state: QueryState,
        question_text: str,
        request: AnswerRequest,
        interpretation: AnswerInterpretation,
    ) -> QueryState:
        try:
            interpretation = AnswerInterpretation.model_validate(interpretation.model_dump())
            turn_id = max((turn.turn_id for turn in state.dialogue_history), default=0) + 1
            known_conditions = {item.condition_id for item in state.condition_states}
            for fact in interpretation.new_facts:
                if fact.source_turn != turn_id or fact.text not in request.answer:
                    raise ValueError("新事实必须逐字摘自本轮回答并标记正确来源轮次")
            for update in interpretation.condition_updates:
                if update.condition_id not in known_conditions:
                    raise ValueError("回答解析不能引入未登记的条件 ID")
                if update.last_updated_turn != turn_id:
                    raise ValueError("条件更新轮次与回答轮次不一致")
                if update.status != ConditionStatus.UNKNOWN and not update.supporting_fact_ids:
                    raise ValueError("非 UNKNOWN 的条件更新必须引用用户事实")

            conditions = {item.condition_id: item for item in state.condition_states}
            conditions.update(
                {item.condition_id: item for item in interpretation.condition_updates}
            )
            payload = {
                **state.model_dump(),
                # 完整回答保存在 DialogueTurn；这里只保存 P4 识别出的事实片段，避免重复。
                "user_facts": [*state.user_facts, *interpretation.new_facts],
                "condition_states": list(conditions.values()),
                "dialogue_history": [
                    *state.dialogue_history,
                    DialogueTurn(
                        turn_id=turn_id,
                        question_id=request.question_id,
                        condition_id=request.condition_id,
                        question=question_text,
                        answer=request.answer,
                    ),
                ],
            }
            # 一次性验证事实、条件和对话，避免 source_turn 的中间态校验失败。
            return QueryState.model_validate(payload)
        except (ValueError, AttributeError, TypeError) as exc:
            raise InvalidComponentOutput("回答解析结果不符合合同或证据引用约束") from exc

    def _run(self, state: QueryState) -> WorkflowSnapshot:
        try:
            snapshot = self.workflow.run(state.model_copy(deep=True))
            snapshot = WorkflowSnapshot.model_validate(snapshot.model_dump())
            actual = snapshot.query_state
            # 投影器可以更新条件，但不能改写会话身份、历史对话和已有原始证据。
            if (
                actual.session_id != state.session_id
                or actual.initial_query != state.initial_query
                or actual.created_at != state.created_at
                or actual.dialogue_history != state.dialogue_history
            ):
                raise ValueError("工作流修改了受保护的会话字段")
            facts = {item.fact_id: item for item in actual.user_facts}
            if any(facts.get(item.fact_id) != item for item in state.user_facts):
                raise ValueError("工作流丢失或改写了既有用户证据")
            return snapshot
        except (
            ValidationError,
            WorkflowInvariantError,
            ValueError,
            AttributeError,
            TypeError,
        ) as exc:
            raise InvalidComponentOutput("工作流结果不符合会话合同") from exc
