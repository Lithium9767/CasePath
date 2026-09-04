from casepath.adapters import (
    DemoCaseRetriever,
    DemoConditionProjector,
    DemoExplanationPlanner,
    DemoQuestionPolicy,
    DemoRuleRetriever,
)
from casepath.workflow import CasePathWorkflow, WorkflowDependencies


def build_demo_workflow() -> CasePathWorkflow:
    return CasePathWorkflow(
        WorkflowDependencies(
            rule_retriever=DemoRuleRetriever(),
            case_retriever=DemoCaseRetriever(),
            condition_projector=DemoConditionProjector(),
            question_policy=DemoQuestionPolicy(),
            explanation_planner=DemoExplanationPlanner(),
        )
    )
