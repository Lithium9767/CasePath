from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import ContractModel, Identifier


class ScoredReference(ContractModel): # 对一个规则、案例或其他对象的带分数引用
    object_id: Identifier # 被检索对象的稳定ID，不在结果中重复保存完整对象
    score: float # 检索或重排得分；是否归一化由具体检索器说明
    reasons: list[str] = Field(default_factory=list) # 该对象进入结果及获得此分数的可解释原因


class RetrievalBundle(ContractModel): # P4一次检索和重排后交给Workflow的统一结果包
    contract_version: Literal["1.1"] = "1.1" # 检索结果合同版本号，只接受1.1
    rule_refs: list[ScoredReference] = Field(default_factory=list) # 与用户问题相关的候选规则
    support_case_refs: list[ScoredReference] = Field(default_factory=list) # 更支持当前解释路径的案例
    limiting_case_refs: list[ScoredReference] = Field(default_factory=list) # 限制或反驳当前路径的案例
    boundary_case_refs: list[ScoredReference] = Field(default_factory=list) # 位于规则适用边界的案例
    cited_span_ids: list[Identifier] = Field(default_factory=list) # 本轮结果已经使用的法条或案例原文ID
    degraded: bool = False # 本轮检索是否因组件或数据不足而降级
    degradation_reason: str | None = None # 降级原因；正常运行时为空
