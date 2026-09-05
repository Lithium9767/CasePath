#规则层
from __future__ import annotations

# 导入日期类型
from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, Identifier, SourceSpan
from .enums import ConditionGroupOperator, MaturityLevel


class ProvisionRef(ContractModel): #规则引用了哪些法条
    source_id: Identifier # 这条法条属于哪部法律
    provision_id: Identifier # 具体法条的唯一编号
    article_no: str # 人类阅读的条号
    title: str # 法条显示名称
    valid_from: date | None = None # 法条生效日期
    valid_to: date | None = None # 法条失效日期


class RuleCondition(ContractModel): # 一个原子条件
# 它描述一个可以单独判断满足、不满足或未知的最小条件。
    condition_id: Identifier # 条件的稳定唯一编号，它是规则层、案例层和用户状态的共同连接点
    label: str = Field(min_length=1) # 简短显示名称
    predicate: str = Field(min_length=1) # 可以被判断真假的完整命题
    user_answerable: bool = True # 用户是否有可能直接回答这个问题
    evidence_types: list[str] = Field(default_factory=list) # 哪些证据可能证明这个条件
    source_span_ids: list[Identifier] = Field(default_factory=list) # 这个条件来自哪些法律原文


class ConditionGroup(ContractModel): # 原子条件如何组合
    """A non-empty AND/OR group over atomic rule conditions."""

    group_id: Identifier # 条件组唯一编号
    label: str = Field(min_length=1) # 条件组显示名称
    operator: ConditionGroupOperator # 组内是全部满足还是任意满足
    member_condition_ids: list[Identifier] = Field(min_length=1) # 组内包含哪些原子条件


class RuleException(ContractModel): # 规则例外，即使一般条件成立，是否存在阻止、排除或限制法律后果的情况。
    exception_id: Identifier # 例外唯一编号
    label: str = Field(min_length=1) # 例外显示名称
    predicate: str = Field(min_length=1) # 需要判断真假的例外命题；
    effect: str = Field(min_length=1) # 这个例外成立后会产生什么影响；
    source_span_ids: list[Identifier] = Field(default_factory=list) # 这个例外来自哪些法律原文


class LegalConsequence(ContractModel): # 法律后果
    consequence_id: Identifier # 后果唯一编号
    consequence_type: str = Field(min_length=1) # 后果类型
    description: str = Field(min_length=1) # 后果描述
    source_span_ids: list[Identifier] = Field(default_factory=list) # 这个后果来自哪些法律原文


class RuleRecord(ContractModel): # 把所有内容装成一条完整规则
    contract_version: Literal["1.1"] = "1.1" # 规则版本号
    rule_id: Identifier # 规则唯一编号
    title: str = Field(min_length=1) # 规则显示名称
    claim_types: list[str] = Field(min_length=1) # 这条规则可能服务于哪些用户请求。
    provisions: list[ProvisionRef] = Field(min_length=1) # 这条规则引用了哪些法条
    conditions: list[RuleCondition] = Field(default_factory=list) # 这条规则包含哪些原子条件
    condition_groups: list[ConditionGroup] = Field(default_factory=list) # 规定这些原子条件怎样组成可计算结构。
    exceptions: list[RuleException] = Field(default_factory=list) # 可能阻止或限制规则适用的例外。
    consequences: list[LegalConsequence] = Field(default_factory=list) # 规则成立后会产生什么法律后果。
    maturity: MaturityLevel # 数据成熟度
    source_spans: list[SourceSpan] = Field(default_factory=list) # 这条规则使用的全部法律原文证据片段。

    # 校验
    @model_validator(mode="after")
    def validate_condition_groups(self) -> RuleRecord:
        condition_ids = [condition.condition_id for condition in self.conditions]
        if len(condition_ids) != len(set(condition_ids)):
            raise ValueError("condition IDs must be unique")

        group_ids = [group.group_id for group in self.condition_groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("condition group IDs must be unique")

        known_condition_ids = set(condition_ids)
        referenced_condition_ids: set[str] = set()
        for group in self.condition_groups:
            member_ids = group.member_condition_ids
            if len(member_ids) != len(set(member_ids)):
                raise ValueError(f"duplicate condition in group: {group.group_id}")

            missing_ids = set(member_ids) - known_condition_ids
            if missing_ids:
                raise ValueError(
                    f"unknown conditions in {group.group_id}: {sorted(missing_ids)}"
                )
            referenced_condition_ids.update(member_ids)

        ungrouped_ids = known_condition_ids - referenced_condition_ids
        if ungrouped_ids:
            raise ValueError(f"conditions are not assigned to a group: {sorted(ungrouped_ids)}")

        return self
