from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, Identifier, ScoreComponent
from .enums import ConditionStatus, SessionStatus


class UserFact(ContractModel): # 从用户初始问题或后续回答中提取的一项事实
    fact_id: Identifier # 用户事实唯一编号，供QueryConditionState.supporting_fact_ids引用
    text: str = Field(min_length=1) # 用户表达该事实时的原话，不能为空
    predicate: str | None = None # 规范化事实类型或命题；无法可靠结构化时允许为空
    value: str | bool | float | None = None # 结构化事实值，可以是文字、真假、数字或空值
    source_turn: int = Field(ge=0) # 事实来源轮次；0表示初始问题，1以后表示追问回答


class CandidateClaim(ContractModel): # 系统根据用户问题识别出的候选请求权
    claim_type: str = Field(min_length=1) # 标准化请求类型，例如“服务合同解除与返还”
    requested_remedy: str = Field(min_length=1) # 结合用户问题形成的具体救济目标
    confidence: float = Field(ge=0, le=1) # 请求权识别置信度，不代表胜诉概率


class QueryConditionState(ContractModel): # 当前用户事实在一个RuleCondition上的投影状态
    condition_id: Identifier # 引用P2定义的RuleCondition.condition_id
    status: ConditionStatus = ConditionStatus.UNKNOWN # 未提供的信息默认UNKNOWN，不能默认不满足
    supporting_fact_ids: list[Identifier] = Field(default_factory=list) # 支持该状态的UserFact编号
    last_updated_turn: int = Field(default=0, ge=0) # 该条件状态最后在哪一轮被更新


class DialogueTurn(ContractModel): # 围绕一个规则条件进行的一轮追问和回答
    turn_id: int = Field(ge=1) # 追问轮次编号，从1开始
    question_id: Identifier # 本轮对应的QuestionCandidate.question_id
    condition_id: Identifier # 本轮问题准备确认的RuleCondition.condition_id
    question: str = Field(min_length=1) # 系统实际向用户展示的问题
    answer: str | None = None # 用户回答原文；尚未回答时为空


class QueryState(ContractModel): # 一个用户咨询会话当前可保存和回放的完整状态
    contract_version: Literal["1.1"] = "1.1" # 用户查询状态合同版本号，只接受1.1
    session_id: Identifier # 用户会话稳定唯一编号
    initial_query: str = Field(min_length=1) # 用户第一次提交的完整问题
    status: SessionStatus = SessionStatus.INITIAL # 当前处于初始、追问、解释、完成或降级阶段
    user_facts: list[UserFact] = Field(default_factory=list) # 从初始问题和后续回答提取的事实
    candidate_claims: list[CandidateClaim] = Field(default_factory=list) # 当前识别出的候选请求权
    condition_states: list[QueryConditionState] = Field(default_factory=list) # 用户对规则条件的状态矩阵
    dialogue_history: list[DialogueTurn] = Field(default_factory=list) # 已经发生的追问和回答历史
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC)) # 自动记录UTC会话创建时间

    @model_validator(mode="after")
    def validate_internal_references(self) -> QueryState:
        # 每种运行时ID在同一会话中都必须唯一。
        fact_ids = [fact.fact_id for fact in self.user_facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("user fact IDs must be unique")

        condition_ids = [item.condition_id for item in self.condition_states]
        if len(condition_ids) != len(set(condition_ids)):
            raise ValueError("query condition IDs must be unique")

        turn_ids = [turn.turn_id for turn in self.dialogue_history]
        if len(turn_ids) != len(set(turn_ids)):
            raise ValueError("dialogue turn IDs must be unique")

        question_ids = [turn.question_id for turn in self.dialogue_history]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("dialogue question IDs must be unique")

        # 条件状态引用的事实必须已经保存在user_facts中。
        known_fact_ids = set(fact_ids)
        for condition in self.condition_states:
            missing_fact_ids = set(condition.supporting_fact_ids) - known_fact_ids
            if missing_fact_ids:
                raise ValueError(
                    f"condition {condition.condition_id} references unknown user facts: "
                    f"{sorted(missing_fact_ids)}"
                )

        # 历史追问必须引用当前条件矩阵中存在的条件。
        known_condition_ids = set(condition_ids)
        for turn in self.dialogue_history:
            if turn.condition_id not in known_condition_ids:
                raise ValueError(
                    f"dialogue turn {turn.turn_id} references an unknown query condition"
                )

        # 事实来源轮次和条件更新时间不能超过会话中已经存在的最大轮次。
        max_turn = max(turn_ids, default=0)
        if any(fact.source_turn > max_turn for fact in self.user_facts):
            raise ValueError("user fact source_turn exceeds the latest dialogue turn")
        if any(item.last_updated_turn > max_turn for item in self.condition_states):
            raise ValueError("condition last_updated_turn exceeds the latest dialogue turn")
        return self


class QuestionCandidate(ContractModel): # P4准备向用户提出的一条高价值澄清问题
    question_id: Identifier # 候选问题唯一编号，AnswerRequest通过它提交回答
    condition_id: Identifier # 该问题准备确认的RuleCondition.condition_id
    question: str = Field(min_length=1) # 面向普通用户展示的自然语言问题
    why_asked: str = Field(min_length=1) # 说明该条件为何会改变法律解释路径
    options: list[str] = Field(min_length=2) # 至少两个预设选项，前端仍可允许自由补充
    utility: float = Field(ge=0, le=1) # 最终追问价值，第一周统一归一化到0至1
    score_components: list[ScoreComponent] = Field(default_factory=list) # 追问价值的可解释分项
    supporting_case_ids: list[Identifier] = Field(default_factory=list) # 支持路径中的代表案例
    limiting_case_ids: list[Identifier] = Field(default_factory=list) # 限制路径中的代表案例
