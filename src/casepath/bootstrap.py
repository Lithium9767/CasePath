from collections.abc import Mapping

from casepath.adapters import (
    BM25CaseRetriever,
    BM25RuleRetriever,
    ConditionProjectionPattern,
    DemoCaseRetriever,
    DemoConditionProjector,
    DemoExplanationPlanner,
    DemoQuestionPolicy,
    DemoRuleRetriever,
    QuestionTemplate,
    RuleConditionProjector,
    WeightedQuestionPolicy,
)
from casepath.contracts import CaseRecord, RuleRecord
from casepath.ports import ExplanationPlanner
from casepath.workflow import CasePathWorkflow, WorkflowDependencies


def build_demo_workflow() -> CasePathWorkflow:
    """构建完全不依赖外部数据的原始演示工作流。"""

    return CasePathWorkflow(
        WorkflowDependencies(
            rule_retriever=DemoRuleRetriever(),
            case_retriever=DemoCaseRetriever(),
            condition_projector=DemoConditionProjector(),
            question_policy=DemoQuestionPolicy(),
            explanation_planner=DemoExplanationPlanner(),
        )
    )


def build_p4_workflow(
    *,
    rules: list[RuleRecord],
    cases: list[CaseRecord],
    projection_patterns: Mapping[str, ConditionProjectionPattern] | None = None,
    question_templates: Mapping[str, QuestionTemplate] | None = None,
    explanation_planner: ExplanationPlanner | None = None,
) -> CasePathWorkflow:
    """使用真实P4算法组件构建可注入数据的工作流。

    P2和P3负责读取、校验并传入RuleRecord与CaseRecord。P4只保存当前数据
    快照并完成检索、投影和追问，不直接访问JSONL、Neo4j或外部LLM。
    `ExplanationPlanner`不属于本轮P4实现范围，因此默认继续使用演示规划器；
    P1或其他负责人提供正式实现后，可以通过参数替换而不修改P4组件。
    """

    rules_by_id = {rule.rule_id: rule for rule in rules}
    cases_by_id = {case.case_id: case for case in cases}
    if len(rules_by_id) != len(rules):
        raise ValueError("构建P4工作流时发现重复规则ID")
    if len(cases_by_id) != len(cases):
        raise ValueError("构建P4工作流时发现重复案例ID")

    selected_explanation_planner = explanation_planner or DemoExplanationPlanner()
    return CasePathWorkflow(
        WorkflowDependencies(
            rule_retriever=BM25RuleRetriever(rules),
            case_retriever=BM25CaseRetriever(cases),
            condition_projector=RuleConditionProjector(
                rules_by_id=rules_by_id,
                patterns=projection_patterns,
            ),
            question_policy=WeightedQuestionPolicy(
                rules_by_id=rules_by_id,
                cases_by_id=cases_by_id,
                templates=question_templates,
            ),
            explanation_planner=selected_explanation_planner,
        )
    )
