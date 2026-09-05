from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, Identifier


class CitationRecord(ContractModel): # 最终解释中的一条可核验引用
    citation_id: Identifier # 引用唯一编号，供ExplanationBranch引用
    source_span_ids: list[Identifier] = Field(min_length=1) # 支持该主张的原文片段，至少一处
    supports: str = Field(min_length=1) # 该引用具体支持解释中的哪项主张
    verified: bool = False # 原文与主张是否已经通过引用核验


class ExplanationBranch(ContractModel): # 当关键事实取不同值时形成的一条条件化解释分支
    branch_id: Identifier # 解释分支唯一编号
    condition: str = Field(min_length=1) # 进入该解释分支所需要的事实条件
    explanation: str = Field(min_length=1) # 该条件下可能得到的法律解释
    citation_ids: list[Identifier] = Field(default_factory=list) # 支持该分支的CitationRecord编号


class EvidenceAction(ContractModel): # 根据未知或争议条件向用户提供的证据准备提示
    action_id: Identifier # 证据行动唯一编号
    description: str = Field(min_length=1) # 用户可以采取的保存、补充或核验证据行动
    related_condition_ids: list[Identifier] = Field(default_factory=list) # 该行动有助于证明哪些规则条件


class ExplanationPlan(ContractModel): # 交给LLM和P5的结构化最终解释计划
    contract_version: Literal["1.1", "1.3"] = "1.3" # 兼容读取v1.1，新结果默认v1.3
    session_id: Identifier # 该解释计划所属的用户会话
    main_explanation: str = Field(min_length=1) # 基于当前信息形成的主要法律解释
    candidate_claims: list[str] = Field(default_factory=list) # 当前识别出的候选请求权
    applicable_rule_ids: list[Identifier] = Field(default_factory=list) # 本次解释使用的规则ID
    support_case_ids: list[Identifier] = Field(default_factory=list) # 支持当前路径的代表案例
    limiting_case_ids: list[Identifier] = Field(default_factory=list) # 限制当前路径的代表案例
    boundary_case_ids: list[Identifier] = Field(default_factory=list) # 规则适用边界的代表案例
    conditional_branches: list[ExplanationBranch] = Field(default_factory=list) # 事实变化时的解释分支
    unresolved_condition_ids: list[Identifier] = Field(default_factory=list) # 仍然未知或冲突的规则条件
    evidence_actions: list[EvidenceAction] = Field(default_factory=list) # 建议用户保存或补充的证据
    citations: list[CitationRecord] = Field(default_factory=list) # 解释引用的法条和案例原文
    disclaimer: str = "该解释基于当前提供的信息，不替代律师针对完整材料出具的法律意见。" # 统一提示

    @model_validator(mode="after")
    def validate_internal_references(self) -> ExplanationPlan:
        # 引用、解释分支和证据行动的ID分别必须唯一。
        citation_ids = [item.citation_id for item in self.citations]
        branch_ids = [item.branch_id for item in self.conditional_branches]
        action_ids = [item.action_id for item in self.evidence_actions]
        id_groups = {
            "citation": citation_ids,
            "explanation branch": branch_ids,
            "evidence action": action_ids,
        }
        for label, identifiers in id_groups.items():
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"{label} IDs must be unique")

        # 条件化解释分支只能引用本计划中已经定义的CitationRecord。
        known_citation_ids = set(citation_ids)
        for branch in self.conditional_branches:
            missing_citation_ids = set(branch.citation_ids) - known_citation_ids
            if missing_citation_ids:
                raise ValueError(
                    f"branch {branch.branch_id} references unknown citations: "
                    f"{sorted(missing_citation_ids)}"
                )
        return self
