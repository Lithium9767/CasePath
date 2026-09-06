"""P1 维护的组件装配入口。

P1 在此选择演示或正式实现，并接入 Neo4j、LLM 等基础设施；P4 提供符合 ports
约定的算法对象，不在本文件中实现评分公式或法律语义判断。
"""

from casepath.adapters import (
    DemoCaseComparator,
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
from casepath.ports import AnswerInterpreter
from casepath.ports.session_repository import SessionRepository
from casepath.workflow import CasePathWorkflow, WorkflowDependencies


def build_session_service(
    *,
    workflow: CasePathWorkflow,
    repository: SessionRepository,
    answer_interpreter: AnswerInterpreter | None,
) -> SessionService:
    """注入正式或测试组件，不在此创建Neo4j/LLM凭据或偷偷回退Demo。"""
    return SessionService(
        repository=repository,
        workflow=workflow,
        answer_interpreter=answer_interpreter,
    )


def build_demo_workflow() -> CasePathWorkflow:
    return CasePathWorkflow(
        WorkflowDependencies(
            rule_retriever=DemoRuleRetriever(),
            condition_projector=DemoConditionProjector(),
            case_retriever=DemoCaseRetriever(),
            case_comparator=DemoCaseComparator(),
            question_policy=DemoQuestionPolicy(),
            explanation_planner=DemoExplanationPlanner(),
        )
    )


def build_demo_session_service() -> SessionService:
    """每个应用实例只调用一次；正式 P4 接入时替换解释器和工作流依赖。"""
    return build_session_service(
        workflow=build_demo_workflow(),
        repository=InMemorySessionRepository(),
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
    demo_reasons = {
        "rule_retriever": "返回固定候选规则，未连接正式索引。",
        "condition_projector": "仅使用关键词投影，未执行正式语义桥接。",
        "case_retriever": "包含占位限制案例，未连接正式案例库。",
        "case_comparator": "分化指标为固定演示值，未执行统计计算。",
        "question_policy": "仅包含一个固定追问模板。",
        "explanation_planner": "仅生成固定条件化解释模板。",
        "answer_interpreter": "只保留回答原文，不执行真实条件语义映射。",
    }
    for name, reason in demo_reasons.items():
        capabilities.append(CapabilityStatus(
            capability=name, available=True, mode=CapabilityMode.DEMO, degraded=True,
            reason=reason,
        ))
    for name in ("legal_graph", "llm", "citation_verification"):
        capabilities.append(CapabilityStatus(
            capability=name, available=False, mode=CapabilityMode.DISABLED,
            reason="尚未接入，不能将结果理解为已核验的法律结论。",
        ))
    return capabilities
