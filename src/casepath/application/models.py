"""只在后端使用的存储模型，不作为 P5 公共响应。"""

from datetime import UTC, datetime

from pydantic import Field, model_validator

from casepath.contracts import AnswerRequest, ContractModel, WorkflowSnapshot
from casepath.contracts.base import Identifier


class AnswerReceipt(ContractModel):
    """保存成功提交的请求和当时结果，使网络重试不重复添加对话。"""

    request: AnswerRequest
    snapshot: WorkflowSnapshot


class SessionRecord(ContractModel):
    """用户状态和待回答问题只保存在快照中，避免多份状态失去同步。"""

    session_id: Identifier
    revision: int = Field(default=0, ge=0)
    latest_snapshot: WorkflowSnapshot
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    answer_receipts: dict[str, AnswerReceipt] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_sessions(self) -> "SessionRecord":
        if self.latest_snapshot.query_state.session_id != self.session_id:
            raise ValueError("record and snapshot must use the same session_id")
        for question_id, receipt in self.answer_receipts.items():
            if question_id != receipt.request.question_id:
                raise ValueError("receipt key must match question_id")
            if receipt.snapshot.query_state.session_id != self.session_id:
                raise ValueError("receipt must belong to the same session")
        return self
