from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, Identifier, ScoreComponent
from .enums import ConditionStatus


class RetrievalPath(ContractModel):
    """一个可解释的图检索路径；节点和边只保存稳定ID或类型。"""

    node_ids: list[Identifier] = Field(min_length=2)
    edge_types: list[Identifier] = Field(min_length=1)
    score: float
    source_span_ids: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_path_shape(self) -> RetrievalPath:
        if len(self.edge_types) != len(self.node_ids) - 1:
            raise ValueError("a retrieval path must have exactly one edge between adjacent nodes")
        return self


class ScoredReference(ContractModel): # 对一个规则、案例或其他对象的带分数引用
    object_id: Identifier # 被检索对象的稳定ID，不在结果中重复保存完整对象
    score: float # 检索或重排得分；是否归一化由具体检索器说明
    reasons: list[str] = Field(default_factory=list) # 面向检索解释的入选理由，不是分数分解
    score_components: list[ScoreComponent] = Field(default_factory=list) # BM25、向量、图路径等数值分项
    retrieval_channels: list[Identifier] = Field(default_factory=list) # 命中的召回通道
    source_span_ids: list[Identifier] = Field(default_factory=list) # 支持入选理由的法条或案例原文
    graph_paths: list[RetrievalPath] = Field(default_factory=list) # 规则约束图路径证据

    @model_validator(mode="after")
    def validate_explanation_components(self) -> ScoredReference:
        component_names = [item.name for item in self.score_components]
        if len(component_names) != len(set(component_names)):
            raise ValueError("score component names must be unique per reference")
        if len(self.retrieval_channels) != len(set(self.retrieval_channels)):
            raise ValueError("retrieval channels must be unique per reference")
        return self


class ConditionComparison(ContractModel):
    """同一规则条件上的案例分化指标，供追问和解释共同消费。"""

    condition_id: Identifier
    status_counts: dict[ConditionStatus, int] = Field(default_factory=dict)
    condition_entropy: float = Field(default=0.0, ge=0.0)
    outcome_mutual_information: float = Field(default=0.0, ge=0.0)
    expected_information_gain: float = Field(default=0.0, ge=0.0)
    expected_rank_change: float = Field(default=0.0, ge=0.0)
    expected_branch_reduction: float = Field(default=0.0, ge=0.0)
    case_disagreement_score: float = Field(default=0.0, ge=0.0, le=1.0)
    rule_condition_discriminativeness: float = Field(default=0.0, ge=0.0, le=1.0)
    support_case_ids: list[Identifier] = Field(default_factory=list)
    limiting_case_ids: list[Identifier] = Field(default_factory=list)
    boundary_case_ids: list[Identifier] = Field(default_factory=list)
    score_components: list[ScoreComponent] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_counts(self) -> ConditionComparison:
        if any(count < 0 for count in self.status_counts.values()):
            raise ValueError("condition status counts must be non-negative")
        return self


class ComparisonBundle(ContractModel):
    """P4案例分化检测的共享结果，避免追问和解释分别重复计算。"""

    contract_version: Literal["1.3"] = "1.3"
    condition_comparisons: list[ConditionComparison] = Field(default_factory=list)
    ranked_condition_ids: list[Identifier] = Field(default_factory=list)
    degraded: bool = False
    degradation_reason: str | None = None

    @model_validator(mode="after")
    def validate_condition_references(self) -> ComparisonBundle:
        condition_ids = [item.condition_id for item in self.condition_comparisons]
        if len(condition_ids) != len(set(condition_ids)):
            raise ValueError("comparison condition IDs must be unique")
        if len(self.ranked_condition_ids) != len(set(self.ranked_condition_ids)):
            raise ValueError("ranked condition IDs must be unique")
        unknown_ranked_ids = set(self.ranked_condition_ids) - set(condition_ids)
        if unknown_ranked_ids:
            raise ValueError(
                f"ranked conditions are missing comparison records: {sorted(unknown_ranked_ids)}"
            )
        return self


class RetrievalBundle(ContractModel): # P4一次检索和重排后交给Workflow的统一结果包
    contract_version: Literal["1.1", "1.3"] = "1.3" # 兼容读取v1.1，新结果默认v1.3
    rule_refs: list[ScoredReference] = Field(default_factory=list) # 与用户问题相关的候选规则
    support_case_refs: list[ScoredReference] = Field(default_factory=list) # 更支持当前解释路径的案例
    limiting_case_refs: list[ScoredReference] = Field(default_factory=list) # 限制或反驳当前路径的案例
    boundary_case_refs: list[ScoredReference] = Field(default_factory=list) # 位于规则适用边界的案例
    cited_span_ids: list[Identifier] = Field(default_factory=list) # 本轮结果已经使用的法条或案例原文ID
    degraded: bool = False # 本轮检索是否因组件或数据不足而降级
    degradation_reason: str | None = None # 降级原因；正常运行时为空

    @model_validator(mode="after")
    def validate_candidate_roles(self) -> RetrievalBundle:
        groups = {
            "rule": [item.object_id for item in self.rule_refs],
            "support": [item.object_id for item in self.support_case_refs],
            "limiting": [item.object_id for item in self.limiting_case_refs],
            "boundary": [item.object_id for item in self.boundary_case_refs],
        }
        for label, identifiers in groups.items():
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"duplicate {label} references")
        case_groups = [set(groups[name]) for name in ("support", "limiting", "boundary")]
        if any(case_groups[left] & case_groups[right] for left, right in ((0, 1), (0, 2), (1, 2))):
            raise ValueError("a case must have exactly one primary comparison role")
        return self
