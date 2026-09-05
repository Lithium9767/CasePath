"""P1 维护的组件装配入口。

P1 在此选择演示或正式实现，并接入 Neo4j、LLM 等基础设施；P4 提供符合 ports
约定的算法对象，不在本文件中实现评分公式或法律语义判断。
"""

from casepath.adapters import (
    DemoCaseRetriever,
    DemoConditionProjector,
    DemoExplanationPlanner,
    DemoQuestionPolicy,
    DemoRuleRetriever,
)
from casepath.adapters.demo_answer_interpreter import DemoAnswerInterpreter
from casepath.adapters.memory_session_repository import InMemorySessionRepository
from casepath.application.session_service import SessionService
from casepath.contracts import CapabilityMode, CapabilityStatus

# 工作流演示
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


def build_demo_session_service() -> SessionService:
    """每个应用实例只调用一次；正式 P4 接入时替换解释器和工作流依赖。"""
    return SessionService(
        repository=InMemorySessionRepository(),
        workflow=build_demo_workflow(),
        answer_interpreter=DemoAnswerInterpreter(),
    )


def build_demo_capabilities() -> list[CapabilityStatus]:
    """只报告真实装配情况，进程健康不等于算法或引用核验可用。"""
    capabilities = [
        CapabilityStatus(
            capability="session_repository", available=True, mode=CapabilityMode.MEMORY,
            reason="单进程内存存储，重启后会话丢失；不支持多个 worker。",
        )
    ]
    for name in (
        "rule_retriever", "case_retriever", "condition_projector",
        "question_policy", "explanation_planner", "answer_interpreter",
    ):
        capabilities.append(CapabilityStatus(
            capability=name, available=True, mode=CapabilityMode.DEMO, degraded=True,
            reason="仅演示联调；回答解释器只保留原文，不执行真实语义映射。",
        ))
    for name in ("legal_graph", "llm", "citation_verification"):
        capabilities.append(CapabilityStatus(
            capability=name, available=False, mode=CapabilityMode.DISABLED,
            reason="尚未接入，不能将结果理解为已核验的法律结论。",
        ))
    return capabilities
