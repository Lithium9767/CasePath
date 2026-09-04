from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import ContractModel, Identifier
from .enums import CapabilityMode, ErrorCode


class AnswerRequest(ContractModel): # P5向P1提交的一次用户追问回答
    contract_version: Literal["1.1"] = "1.1" # 回答请求合同版本号，只接受1.1
    question_id: Identifier # 用户正在回答的QuestionCandidate.question_id
    condition_id: Identifier # 该问题对应的RuleCondition.condition_id
    answer: str = Field(min_length=1) # 用户回答原文，由P4进一步投影为ConditionStatus
    selected_option: str | None = None # 用户选择的预设选项；自由回答时允许为空


class ErrorResponse(ContractModel): # 所有正式API共同使用的统一错误响应
    contract_version: Literal["1.1"] = "1.1" # 错误响应合同版本号，只接受1.1
    code: ErrorCode # 稳定机器错误码，供P5决定展示和重试方式
    message: str = Field(min_length=1) # 面向开发者或用户的简要错误说明
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict) # 错误上下文
    retryable: bool = False # 当前请求是否适合在不修改输入的情况下重试
    request_id: Identifier | None = None # 服务端请求编号，用于日志排查


class CapabilityStatus(ContractModel): # 系统中一个组件或能力当前是否可用
    contract_version: Literal["1.1"] = "1.1" # 能力状态合同版本号，只接受1.1
    capability: Identifier # 能力名称，例如legal_graph、rule_retriever或llm
    available: bool # 该能力当前能否为请求提供结果
    mode: CapabilityMode # 当前使用真实、演示、内存降级还是关闭模式
    degraded: bool = False # 是否正在以低于完整能力的方式运行
    reason: str | None = None # 不可用或降级的具体原因
