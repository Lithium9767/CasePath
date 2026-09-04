#案例层
# 把一份判决书拆成“请求—法院认定—规则条件桥接—裁判理由—裁判结果—原文证据”。
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from .base import Confidence, ContractModel, Identifier, SourceSpan
from .enums import ConditionStatus, DecisionStatus, MaturityLevel


class ClaimRecord(ContractModel): # 当事人提出的一项具体请求
    claim_id: Identifier # 请求唯一编号
    claim_type: str = Field(min_length=1) # 标准化请求类型，例如“服务合同解除与返还”
    claimant: str | None = None # 提出请求的人；无法可靠提取时允许为空
    respondent: str | None = None # 请求针对的人；无法可靠提取时允许为空
    requested_remedy: str = Field(min_length=1) # 当事人具体希望法院作出的处理
    amount: float | None = Field(default=None, ge=0) # 请求金额；无金额或未提取时为空
    invoked_rule_ids: list[Identifier] = Field(default_factory=list) # 该请求关联或援引的规则ID


class CourtFinding(ContractModel): # 法院认定的一项事实，不是当事人的单方陈述
    finding_id: Identifier # 法院事实认定的唯一编号
    predicate: str = Field(min_length=1) # 可被肯定或否定的结构化事实命题
    polarity: bool | None = None # True为肯定、False为否定、None为无法确认
    source_span_ids: list[Identifier] = Field(min_length=1) # 支持该认定的判决书原文，至少一处


class ConditionFinding(ContractModel): # 历史案例事实到规则条件的语义桥接
    condition_id: Identifier # 引用P2定义的RuleCondition.condition_id
    status: ConditionStatus # 本案例对该条件是满足、不满足、未知、冲突或不适用
    finding_ids: list[Identifier] = Field(default_factory=list) # 支持该状态的CourtFinding编号
    confidence: Confidence # 桥接可靠程度，范围0到1，不代表胜诉概率
    source_span_ids: list[Identifier] = Field(default_factory=list) # 桥接判断对应的判决书原文
    human_verified: bool = False # 是否已经由人工核验；机器候选默认False


class ReasoningStep(ContractModel): # 法院从事实和规则推导到结论的一步理由
    reasoning_id: Identifier # 推理步骤唯一编号
    premise_finding_ids: list[Identifier] = Field(default_factory=list) # 本步骤依赖的法院事实认定ID
    applied_rule_ids: list[Identifier] = Field(default_factory=list) # 本步骤适用的结构化法律规则ID
    conclusion: str = Field(min_length=1) # 本步骤得出的中间或最终法律结论
    source_span_ids: list[Identifier] = Field(min_length=1) # 支持该推理步骤的裁判理由原文


class DecisionItem(ContractModel): # 法院对某一项ClaimRecord的裁判结果
    decision_id: Identifier # 裁判结果项唯一编号
    claim_id: Identifier # 指向本结果处理的ClaimRecord.claim_id
    status: DecisionStatus # 全部支持、部分支持、驳回、撤回或未知
    description: str = Field(min_length=1) # 对判决主文的结构化描述
    amount: float | None = Field(default=None, ge=0) # 法院实际支持金额，不是当事人请求金额
    source_span_ids: list[Identifier] = Field(min_length=1) # 对应判决主文原文，至少一处


class CaseRecord(ContractModel): # 一件完整的结构化案例，是案例层顶层对象
    contract_version: Literal["1.1"] = "1.1" # 当前案例合同版本号，只接受1.1
    case_id: Identifier # 案例稳定唯一编号
    title: str = Field(min_length=1) # 案例显示名称，不能为空
    case_no: str | None = None # 法院案号；缺失或无法可靠提取时为空
    court: str | None = None # 作出裁判的法院
    judgment_date: date | None = None # 裁判日期，用于匹配当时有效的法律版本
    cause: str | None = None # 案由，例如“服务合同纠纷”
    maturity: MaturityLevel # 案例结构化和人工核验的成熟度L0至L3
    claims: list[ClaimRecord] = Field(min_length=1) # 当事人的请求列表，至少一项
    findings: list[CourtFinding] = Field(default_factory=list) # 法院认定事实列表
    condition_findings: list[ConditionFinding] = Field(default_factory=list) # 案例到规则条件的桥接
    reasoning_steps: list[ReasoningStep] = Field(default_factory=list) # 法院裁判推理步骤列表
    decisions: list[DecisionItem] = Field(default_factory=list) # 法院对各项请求的裁判结果
    source_spans: list[SourceSpan] = Field(default_factory=list) # 本案例被引用的全部判决书原文片段

    @model_validator(mode="after")
    def validate_internal_references(self) -> CaseRecord:
        # 同一案例中各类实体ID必须唯一，避免图导入时节点互相覆盖。
        claim_ids = [claim.claim_id for claim in self.claims]
        finding_ids = [finding.finding_id for finding in self.findings]
        condition_ids = [item.condition_id for item in self.condition_findings]
        reasoning_ids = [step.reasoning_id for step in self.reasoning_steps]
        decision_ids = [decision.decision_id for decision in self.decisions]
        span_ids = [span.span_id for span in self.source_spans]

        id_groups = {
            "claim": claim_ids,
            "finding": finding_ids,
            "condition finding": condition_ids,
            "reasoning": reasoning_ids,
            "decision": decision_ids,
            "source span": span_ids,
        }
        for label, identifiers in id_groups.items():
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"{label} IDs must be unique")

        known_claim_ids = set(claim_ids)
        known_finding_ids = set(finding_ids)
        known_span_ids = set(span_ids)

        # ConditionFinding和ReasoningStep只能引用本案例已经定义的CourtFinding。
        for item in self.condition_findings:
            missing_finding_ids = set(item.finding_ids) - known_finding_ids
            if missing_finding_ids:
                raise ValueError(
                    f"condition {item.condition_id} references unknown findings: "
                    f"{sorted(missing_finding_ids)}"
                )
        for step in self.reasoning_steps:
            missing_finding_ids = set(step.premise_finding_ids) - known_finding_ids
            if missing_finding_ids:
                raise ValueError(
                    f"reasoning {step.reasoning_id} references unknown findings: "
                    f"{sorted(missing_finding_ids)}"
                )

        # 每项裁判结果必须对应本案例中真实存在的一项请求。
        for decision in self.decisions:
            if decision.claim_id not in known_claim_ids:
                raise ValueError(
                    f"decision {decision.decision_id} references an unknown claim"
                )

        # 所有判决书引用必须能在本案例source_spans中找到。
        span_references: list[tuple[str, list[Identifier]]] = []
        span_references.extend(
            (f"finding {item.finding_id}", item.source_span_ids) for item in self.findings
        )
        span_references.extend(
            (f"condition {item.condition_id}", item.source_span_ids)
            for item in self.condition_findings
        )
        span_references.extend(
            (f"reasoning {item.reasoning_id}", item.source_span_ids)
            for item in self.reasoning_steps
        )
        span_references.extend(
            (f"decision {item.decision_id}", item.source_span_ids) for item in self.decisions
        )
        for owner, referenced_span_ids in span_references:
            missing_span_ids = set(referenced_span_ids) - known_span_ids
            if missing_span_ids:
                raise ValueError(
                    f"{owner} references unknown source spans: {sorted(missing_span_ids)}"
                )
        return self
