"""P1 维护的单轮工作流编排。

本文件只负责端口调用顺序、追问预算和输出一致性，不实现 P4 的混合召回、
条件语义映射、图路径评分、案例分化检测或信息增益算法。
"""

from __future__ import annotations

from dataclasses import dataclass

from casepath.application.errors import QuestionGenerationFailed
from casepath.contracts import (
    QueryState,
    SessionStatus,
    WorkflowSnapshot,
)
from casepath.ports import (
    CaseComparator,
    CaseRetriever,
    ConditionProjector,
    ExplanationPlanner,
    QuestionPolicy,
    RuleRetriever,
)


class WorkflowInvariantError(RuntimeError):
    """工作流依赖返回了违反编排约定的结果。"""

# dataclass自动生成样本代码，frozen=True 表示这个对象创建后，字段不允许重新赋值
@dataclass(frozen=True)
class WorkflowDependencies:
    rule_retriever: RuleRetriever
    condition_projector: ConditionProjector
    case_retriever: CaseRetriever
    case_comparator: CaseComparator
    question_policy: QuestionPolicy
    explanation_planner: ExplanationPlanner


class CasePathWorkflow:
    """Deterministic orchestration; adapters may use databases or LLMs behind ports."""

    # __init__：把dependencies和max_question_turns默认值存下来
    # * 表示后面的参数必须写名字
    # WorkflowDependencies 是端口依赖注入，是插槽盒，由bootstrap注入
    def __init__(self, dependencies: WorkflowDependencies, *, max_question_turns: int = 5) -> None:
        # 轮数上限是交互预算，不意味着未知条件已经满足。
        if max_question_turns < 0:
            raise ValueError("max_question_turns must be non-negative")
        self.dependencies = dependencies
        self.max_question_turns = max_question_turns

    # CasePath 的执行主流程
    def run(self, state: QueryState) -> WorkflowSnapshot:
        # 【每轮请求入口】首次问题和补充回答都走这里。state 是内存对象，不是 HTTP 请求。
        # 接口声明看 ports，具体实现看 adapters/p4_factory.py 的装配表。
        # 日志只记录真实调用，不将未实现的解析、案例分化或引用核验写成已完成。
        # 初始化日志
        trace = []
        # 【首次请求 06 / A：规则召回】输入 state，输出 rule_refs（候选规则 ID、分数、来源）。
        # 返回后继续 B，不把整个规则对象复制进状态；具体内容由 catalog 按 ID 取得。
        rule_refs = self.dependencies.rule_retriever.retrieve(state)
        # 【A 跳转】有 LLM 时先进入 retrieval/rule_relevance.py:RelevantRuleRetriever.retrieve，
        # 它调用 retrieval/hybrid_cypher.py:HybridCypherRuleRetriever.retrieve 后再筛主题。
        # 无 LLM 时直接进入 HybridCypherRuleRetriever.retrieve。
        trace.append("RETRIEVE_RULES")

        # 【首次请求 07 / B：条件投影】输入 state + rule_refs，输出补充条件状态的 projected。
        # 先有候选规则才能限定条件空间；这里不需要先完成案例检索。
        projected = self.dependencies.condition_projector.project(state, rule_refs)
        # 【B 跳转】conditions/semantic_condition_projector.py:SemanticConditionProjector.project。
        # projected 仍是 QueryState，只是补充了事实/条件；候选条件来自前一步规则。
        trace.append("PROJECT_QUERY")

        # 【首次请求 08 / C：案例检索】输入 projected + rule_refs，输出 RetrievalBundle。
        # 其中有规则引用和支持/限制/边界案例引用，供 D 比较，不是最终解释文本。
        bundle = self.dependencies.case_retriever.retrieve(projected, rule_refs)
        # 【C 跳转】retrieval/hybrid_cypher.py:HybridCypherCaseRetriever.retrieve，
        # 其中的图访问继续跳到 retrieval/graph_navigator.py，搜索访问 search 对象。
        trace.append("RETRIEVE_CASES")

        # 【首次请求 09 / D：条件比较】输入 projected + bundle，输出 comparison。
        # 统计相同条件上的案例状态分布；接下来交给 E 判断哪些未知条件值得追问。
        comparison = self.dependencies.case_comparator.compare(projected, bundle)
        # 【D 跳转】conditions/condition_comparator.py:ConditionComparator.compare。
        # comparison 是比较结果，不改成用户事实，也不是最终回答。
        trace.append("COMPARE_CASES")

        # 【首次请求 10 / E：追问选择】先检查预算；未达上限才调用 select。
        # 输入 projected + bundle + comparison，输出 QuestionCandidate 或 None。
        question_generation_failed = False
        if len(projected.dialogue_history) >= self.max_question_turns:
            question = None
            trace.append("QUESTION_BUDGET_REACHED")
        else:
            # 如果这道问题以前已经问过，或者它对应的条件以前已经问过，就报错。
            try:
                question = self.dependencies.question_policy.select(projected, bundle, comparison)
            except QuestionGenerationFailed:
                # 仅恢复追问输出校验失败；网络错误仍抛出，不能伪装成功。
                question = None
                question_generation_failed = True
                trace.append("QUESTION_GENERATION_DEGRADED")
            # 【E 跳转】questioning/key_condition_policy.py:KeyConditionQuestionPolicy.select，
            # 按关键缺失条件排序，LLM生成简短事实问题；不执行回答模拟。
            # 策略负责过滤历史；这里仅守住“不能重复发问”的系统不变量。
            if question is not None and any(
                turn.question_id == question.question_id
                or turn.condition_id == question.condition_id
                # 循环检查
                for turn in projected.dialogue_history
            ):
                raise WorkflowInvariantError("question policy selected an answered question")
        # 如果问题已经问完了，则进入解释阶段，调整状态为READY_TO_EXPLAIN
        if question is None:
            # 无追问可能是预算用完、收益低或没有问题卡，并不意味着未知条件全部满足。
            projected = projected.model_copy(update={"status": SessionStatus.READY_TO_EXPLAIN})
            trace.append("STOP_CLARIFICATION")
        else:
            # 如果还有问题，NEEDS_CLARIFICATION
            projected = projected.model_copy(update={"status": SessionStatus.NEEDS_CLARIFICATION})
            trace.append("SCORE_QUESTIONS")

        # 【首次请求 11a：解释规划】先根据 question 设置状态，再用状态和证据生成 plan。
        # 有问题时输出澄清短说明，无问题时进入完整解释路径；question 本身单独返回给前端。
        plan = self.dependencies.explanation_planner.build(projected, bundle, comparison)
        if question_generation_failed:
            plan = plan.model_copy(update={"main_explanation": (
                "本轮暂未生成合格的追问，以下仅按已有信息说明；"
                "尚不明确的条件仍保留，不代表事实已经齐全。\n\n" + plan.main_explanation
            )})
        # 【解释跳转】explanation/evidence_explanation_planner.py:EvidenceExplanationPlanner.build。
        # 需要完整 LLM 解释时再进 grounded_explanation.py:generate_explanation。
        # 澄清状态下解释器返回短说明；完整 LLM 解释不必在每一轮追问前执行。
        trace.append("BUILD_EXPLANATION_PLAN")
        # 【首次请求 11b：打包单轮结果】以下字段共同组成 WorkflowSnapshot。
        # 返回 application/session_service.py 的 _run，继续第 12 步校验，然后才保存会话。
        return WorkflowSnapshot(
            # 【单轮返回】回到 application/session_service.py:_run 做一致性校验，
            # 不是从这里直接写数据库或直接向浏览器发 HTTP 响应。
            # API 将快照序列化为 JSON；前端分别展示条件、案例、问题和解释，不只接收字符串。
            query_state=projected,
            retrieval_bundle=bundle,
            comparison_bundle=comparison,
            next_question=question,
            explanation_plan=plan,
            trace=trace,
        )
