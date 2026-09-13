"""P4 实现区：把用户回答投影为事实和条件更新。

动态事实问题不直接「是→条件满足」。无法回答是回答属性，不是条件否定。
事实必须逐字摘自本轮回答。虚构原话或未知 condition_id 直接拒绝。
明确纠正（含金额更正）写入 corrections；普通相反描述不自动纠正。
"""

from __future__ import annotations

import re

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
    CorrectionOperation,
    QueryState,
    QuestionCandidate,
    UserFact,
)

_UNKNOWN = ("不清楚", "不知道", "不了解", "不确定", "说不清")
_DECLINED = ("暂不回答", "不想说", "拒答")
_BARE_NO = frozenset({"没有", "否", "不是", "尚未", "无"})
_BARE_YES = frozenset({"是", "对"})
_CORRECTION_MARKERS = ("说错了", "更正", "纠正", "改成", "应为", "其实是", "应该是")
_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*元?")
_MONEY_CUES = ("元", "交了", "付了", "付的", "支付", "付款", "充了", "金额", "费用")
_AMOUNT_CORRECTION_CUES = ("金额", "元", "交了", "付了", "付的", "费用")
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


def _classify_disposition(selected: str, answer: str) -> str:
    """区分整句无法回答、拒绝，以及「不清楚但还有事实」的混合回答。"""
    text = answer.strip()
    choice = selected.strip()
    if choice in _DECLINED or text in _DECLINED:
        return "DECLINED"
    if choice in _UNKNOWN or text in _UNKNOWN:
        return "UNABLE"
    if any(token == text for token in _UNKNOWN):
        return "UNABLE"
    if any(token in text for token in _UNKNOWN) and len(text) > max(len(token) for token in _UNKNOWN) + 2:
        return "MIXED"
    return "FACTS_PROVIDED"


def _status(pending: QuestionCandidate, request: AnswerRequest) -> ConditionStatus:
    # 先用 condition_id + 完整选项解释回答，再处理不知道、裸是/否及条件专用自由文本。
    # 这是确定性解释器，不调用 LLM；无法识别保留 UNKNOWN，不猜测用户意思。
    selected = (request.selected_option or "").strip()
    answer = request.answer.strip()
    if selected:
        mapped = option_status(pending.condition_id, selected)
        if mapped is not None:
            return mapped
        if selected in _UNKNOWN or selected in _DECLINED:
            return ConditionStatus.UNKNOWN
        return ConditionStatus.UNKNOWN
    mapped = option_status(pending.condition_id, answer)
    if mapped is not None:
        return mapped
    if any(token == answer or token in answer for token in (*_UNKNOWN, *_DECLINED)):
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


def _explicit_correction(
    state: QueryState,
    *,
    answer: str,
    replacement: UserFact,
    condition_id: str,
) -> list[CorrectionOperation]:
    """仅在用户明确更正时产生纠正；普通矛盾描述返回空。

    段落事实按「事实 ID + 条件」粒度退出旧映射，不整条删除段落中的其他事实断言。
    目标选择优先本条件已有映射；金额纠正不得仅因“含数字”误伤课时等无关事实。
    无法唯一/可靠锁定目标时返回空，而不是猜测。
    """
    if not any(marker in answer for marker in _CORRECTION_MARKERS):
        return []
    # 金额对象必须区分；暂未有独立已退款条件时，保守不自动撤销旧证据。
    balance_correction = any(word in answer for word in ("余额", "剩余", "未消费"))
    refund_correction = any(word in answer for word in ("已退款", "已退", "退了", "退还了"))
    if refund_correction:
        return []
    if balance_correction:
        condition_id = "cond.unperformed_balance"
    linked_ids = {
        evidence.fact_id
        for item in state.condition_states
        if item.condition_id == condition_id
        for evidence in item.evidence
    }
    facts_by_id = {item.fact_id: item for item in state.user_facts}
    linked_facts = [facts_by_id[fid] for fid in linked_ids if fid in facts_by_id]
    answer_amounts = _AMOUNT.findall(answer)
    amount_correction = bool(answer_amounts) and any(
        cue in answer for cue in _AMOUNT_CORRECTION_CUES
    )

    targets: list[UserFact] = []
    if amount_correction:
        payment_linked_ids = {
            evidence.fact_id
            for item in state.condition_states
            if item.condition_id == (
                "cond.unperformed_balance" if balance_correction else "cond.payment_made"
            )
            for evidence in item.evidence
        }
        payment_linked = [facts_by_id[fid] for fid in payment_linked_ids if fid in facts_by_id]
        # 1) 优先付款条件已有映射，且含与新金额冲突的旧数字
        for fact in payment_linked or linked_facts:
            fact_amounts = _AMOUNT.findall(fact.text)
            if fact_amounts and any(amount not in answer_amounts for amount in fact_amounts):
                targets.append(fact)
        # 2) 无已映射目标时，仅回退到带金钱线索且金额冲突的事实，不用裸数字事实
        if not targets:
            if balance_correction:
                return []  # 不扫描其他付款事实来猜测余额目标。
            for fact in state.user_facts:
                if not any(cue in fact.text for cue in _MONEY_CUES):
                    continue
                fact_amounts = _AMOUNT.findall(fact.text)
                if fact_amounts and any(amount not in answer_amounts for amount in fact_amounts):
                    targets.append(fact)
    else:
        # 非金额明确纠正：只能纠正本条件已引用的事实，禁止扫描会话里任意最近事实
        targets = list(linked_facts)

    if not targets:
        return []
    if len(targets) != 1:
        # 多个候选没有可靠指代时，不以时间最近代替语义定位。
        return []
    target = targets[0]
    # 金额纠正绑定付款条件，而不是“当前被问到的任意条件”。
    link_condition = condition_id
    if amount_correction and not balance_correction and any(
        item.condition_id == "cond.payment_made" for item in state.condition_states
    ):
        link_condition = "cond.payment_made"
    return [
        CorrectionOperation(
            target_fact_id=target.fact_id,
            replacement_fact_id=replacement.fact_id,
            condition_id=link_condition,
            reason="用户明确纠正本轮相关事实",
        )
    ]


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
        selected = (answer_request.selected_option or "").strip()
        disposition = _classify_disposition(selected, quote)
        fact = UserFact(
            fact_id=f"fact.answer.{turn_id}.{pending_question.condition_id}",
            text=quote,
            source_turn=turn_id,
        )
        corrections = _explicit_correction(
            state,
            answer=quote,
            replacement=fact,
            condition_id=pending_question.condition_id,
        )
        if disposition == "DECLINED":
            # 拒绝补充：不改条件状态，也不把拒答当反对证据。
            return AnswerInterpretation(
                disposition="DECLINED",
                declined_condition_ids=[pending_question.condition_id],
                new_facts=[fact],
            )
        if disposition == "UNABLE":
            # 单独「不清楚」：保留审计事实，条件状态交给 B 保持旧证据，不在此写否定。
            return AnswerInterpretation(
                disposition="UNABLE",
                unable_condition_ids=[pending_question.condition_id],
                new_facts=[fact],
            )
        if pending_question.question_id.startswith("question.fact."):
            # 动态问题问的是具体事实，“有”不等于目标法律条件满足。
            return AnswerInterpretation(
                disposition=disposition,
                new_facts=[fact],
                corrections=corrections,
            )
        existing_texts = {
            (item.condition_id, fact_id)
            for item in state.condition_states
            for fact_id in item.supporting_fact_ids
        }
        same = [
            prior
            for prior in state.user_facts
            if prior.text == quote
            and (pending_question.condition_id, prior.fact_id) in existing_texts
        ]
        if same:
            return AnswerInterpretation(disposition=disposition, corrections=corrections)
        status = _status(pending_question, answer_request)
        if status is ConditionStatus.UNKNOWN:
            return AnswerInterpretation(
                disposition=disposition,
                new_facts=[fact],
                corrections=corrections,
            )
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
        # 明确纠正时，旧目标事实不参与本轮条件聚合。
        corrected_targets = {item.target_fact_id for item in corrections}
        evidence = [item for item in evidence if item.fact_id not in corrected_targets]
        update = _aggregate(
            pending_question.condition_id,
            evidence,
            reasons=["保留历史证据；不同事实相反时标记冲突"],
        )
        update.last_updated_turn = turn_id
        return AnswerInterpretation(
            disposition=disposition,
            new_facts=[fact],
            condition_updates=[update],
            corrections=corrections,
        )
