"""P4 实现区：把用户回答投影为事实和条件更新。

选项采用 `condition_id + 完整选项 → 状态`。自由回答先处理否定作用范围，
无法可靠判断时保持 UNKNOWN。不用一套通用肯定词判断所有条件。
事实必须逐字摘自本轮回答。虚构原话或未知 condition_id 直接拒绝。
"""

from __future__ import annotations

from casepath.adapters.conditions.semantic_condition_projector import (
    _aggregate,
    _is_intent_or_conditional,
)
from casepath.adapters.questioning.question_cards import option_status
from casepath.contracts import (
    AnswerInterpretation,
    AnswerRequest,
    ConditionEvidence,
    ConditionStatus,
    QueryState,
    QuestionCandidate,
    UserFact,
)

_UNKNOWN = ("不清楚", "不知道", "不了解", "不确定", "说不清")
_BARE_NO = frozenset({"没有", "否", "不是", "尚未", "无"})
_BARE_YES = frozenset({"是", "对"})
_FREE_TEXT: dict[str, dict[ConditionStatus, tuple[str, ...]]] = {
    "cond.payment_made": {
        ConditionStatus.NOT_SATISFIED: (
            "没有支付",
            "没有付款",
            "还没付款",
            "还没付",
            "尚未支付",
            "未付款",
            "没付款",
        ),
        ConditionStatus.SATISFIED: ("已经支付", "已经付款", "付过了", "交过了"),
    },
    "cond.unperformed_balance": {
        ConditionStatus.NOT_SATISFIED: ("已经全部消费", "全部消费", "消费完了", "没有余额"),
        ConditionStatus.SATISFIED: ("还有未消费", "还有余额", "未消费余额", "没消费完"),
    },
    "cond.contract_terminated": {
        ConditionStatus.NOT_SATISFIED: ("尚未解除", "没有解除", "未解除"),
        ConditionStatus.SATISFIED: ("已经解除", "解除了", "通知解除"),
    },
    "cond.performance_impossible": {
        ConditionStatus.NOT_SATISFIED: ("仍能继续", "还能履行", "可以到其他"),
        ConditionStatus.SATISFIED: ("已经无法继续", "无法继续履行", "永久关闭", "已经关了"),
    },
    "cond.alternative_performance": {
        ConditionStatus.NOT_SATISFIED: ("没有可接受", "没有替代", "不能转店"),
        ConditionStatus.SATISFIED: ("有可接受的替代", "可以转店", "其他门店"),
    },
    "cond.contract_exists": {
        ConditionStatus.NOT_SATISFIED: ("没有成立", "没有合同", "未签订"),
        ConditionStatus.SATISFIED: ("已经成立", "签了合同", "办了卡"),
    },
}


def _longest_hits(text: str, phrases: tuple[str, ...]) -> str | None:
    found = [phrase for phrase in phrases if phrase and phrase in text]
    if not found:
        return None
    return max(found, key=len)


def _status(pending: QuestionCandidate, request: AnswerRequest) -> ConditionStatus:
    # 先用 condition_id + 完整选项解释回答，再处理不知道、裸是/否及条件专用自由文本。
    # 这是确定性解释器，不调用 LLM；无法识别保留 UNKNOWN，不猜测用户意思。
    selected = (request.selected_option or "").strip()
    answer = request.answer.strip()
    if selected:
        mapped = option_status(pending.condition_id, selected)
        if mapped is not None:
            return mapped
        return ConditionStatus.UNKNOWN
    mapped = option_status(pending.condition_id, answer)
    if mapped is not None:
        return mapped
    if any(token == answer or token in answer for token in _UNKNOWN):
        return ConditionStatus.UNKNOWN
    if pending.condition_id == "cond.contract_terminated" and answer in (_BARE_NO | _BARE_YES):
        # 日常沟通问题的“是/没有”不足以认定法律关系是否已结束。
        return ConditionStatus.UNKNOWN
    if answer in _BARE_NO:
        return ConditionStatus.NOT_SATISFIED
    if answer in _BARE_YES:
        return ConditionStatus.SATISFIED
    if _is_intent_or_conditional(answer):
        return ConditionStatus.UNKNOWN
    patterns = _FREE_TEXT.get(pending.condition_id, {})
    negative = _longest_hits(answer, patterns.get(ConditionStatus.NOT_SATISFIED, ()))
    positive = _longest_hits(answer, patterns.get(ConditionStatus.SATISFIED, ()))
    if negative and positive:
        return ConditionStatus.UNKNOWN
    if negative:
        return ConditionStatus.NOT_SATISFIED
    if positive:
        return ConditionStatus.SATISFIED
    return ConditionStatus.UNKNOWN


class SemanticAnswerInterpreter:
    def interpret(
        self,
        state: QueryState,
        pending_question: QuestionCandidate,
        answer_request: AnswerRequest,
    ) -> AnswerInterpretation:
        # 【用户回答后】产出新事实和条件更新交给 SessionService 合并，自己不改仓库。
        # 之后重跑 B 时，新增原话还能参与其他候选条件的映射，不限于被问的条件。
        if answer_request.condition_id != pending_question.condition_id:
            raise ValueError("回答解析不能引入未登记的条件 ID")
        known = {item.condition_id for item in state.condition_states}
        if pending_question.condition_id not in known:
            raise ValueError("回答解析不能引入未登记的条件 ID")
        quote = answer_request.answer.strip()
        if quote not in answer_request.answer:
            raise ValueError("新事实必须逐字摘自本轮回答")
        turn_id = max((turn.turn_id for turn in state.dialogue_history), default=0) + 1
        if pending_question.question_id.startswith("question.fact."):
            # 动态问题问的是具体事实，“有”不等于目标法律条件满足。
            # 只保存原话；SessionService保存问题上下文后，B统一解释本轮回答。
            return AnswerInterpretation(new_facts=[UserFact(
                fact_id=f"fact.answer.{turn_id}.{pending_question.condition_id}",
                text=quote, source_turn=turn_id,
            )])
        existing_texts = {
            (item.condition_id, fact_id)
            for item in state.condition_states
            for fact_id in item.supporting_fact_ids
        }
        same = [
            fact
            for fact in state.user_facts
            if fact.text == quote
            and (pending_question.condition_id, fact.fact_id) in existing_texts
        ]
        if same:
            # 同条件已经引用相同原话时，不重复增加事实和分数；对话回执仍由服务层负责。
            return AnswerInterpretation()
        status = _status(pending_question, answer_request)
        fact = UserFact(
            fact_id=f"fact.answer.{turn_id}.{pending_question.condition_id}",
            text=quote,
            source_turn=turn_id,
        )
        if status is ConditionStatus.UNKNOWN:
            # 没回答清当前问题，不代表整段回答没有其他有用事实。
            return AnswerInterpretation(new_facts=[fact])
        relation = "SUPPORTS" if status is ConditionStatus.SATISFIED else "OPPOSES"
        old = next(
            item
            for item in state.condition_states
            if item.condition_id == pending_question.condition_id
        )
        evidence = [
            *old.evidence,
            ConditionEvidence(
                fact_id=fact.fact_id,
                relation=relation,
                confidence=0.7,
                reason="本轮问题选项/自由回答映射；分数未经过标注集校准",
            ),
        ]
        update = _aggregate(
            pending_question.condition_id,
            evidence,
            reasons=["保留历史证据；不同事实相反时标记冲突"],
        )
        update.last_updated_turn = turn_id
        return AnswerInterpretation(
            new_facts=[fact],
            condition_updates=[update],
        )
