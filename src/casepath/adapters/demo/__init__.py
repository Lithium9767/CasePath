"""离线演示与测试脚手架（非正式算法路径）。

读代码时：
- 正式 Live：`adapters/{infrastructure,retrieval,conditions,questioning,explanation}` + `p4_factory` + `bootstrap.build_live_runtime`
- 离线 Demo：本包 `components` / `wiring`
"""

from casepath.adapters.demo.answer_interpreter import DemoAnswerInterpreter
from casepath.adapters.demo.components import (
    DEMO_LIMITING_CASE_ID,
    DEMO_RULE_ID,
    DEMO_SUPPORT_CASE_ID,
    DemoCaseComparator,
    DemoCaseRetriever,
    DemoConditionProjector,
    DemoExplanationPlanner,
    DemoQuestionPolicy,
    DemoRuleRetriever,
)
from casepath.adapters.demo.wiring import (
    build_demo_capabilities,
    build_demo_session_service,
    build_demo_workflow,
)

__all__ = [
    "DEMO_LIMITING_CASE_ID",
    "DEMO_RULE_ID",
    "DEMO_SUPPORT_CASE_ID",
    "DemoAnswerInterpreter",
    "DemoCaseComparator",
    "DemoCaseRetriever",
    "DemoConditionProjector",
    "DemoExplanationPlanner",
    "DemoQuestionPolicy",
    "DemoRuleRetriever",
    "build_demo_capabilities",
    "build_demo_session_service",
    "build_demo_workflow",
]
