"""P4 实现区：追问卡片与选项到条件状态的显式映射。

选项按 `condition_id + 完整选项文本` 对应状态，不用通用肯定词套所有条件。
"""

from __future__ import annotations

from casepath.contracts import ConditionStatus

QUESTION_CARDS: dict[str, tuple[str, list[str], str]] = {
    "cond.main_obligation_delayed": (
        "原来说好的服务时间到了吗？对方有没有按时提供服务？",
        ["时间已到，对方还没提供", "已经按时提供", "还没到说好的时间", "不清楚"],
        "先分清是还没到时间，还是对方已经耽误了约定的服务。",
    ),
    "cond.demand_delivered": (
        "你有没有明确要求对方继续提供服务？有没有聊天回复等记录，能说明对方收到了这个要求？",
        ["要求过，有对方收到的记录", "还没要求过", "要求过，但不确定对方收到", "不清楚"],
        "要求继续服务和要求退钱是两件事，我想先弄清你们沟通过什么。",
    ),
    "cond.reasonable_period_expired": (
        "你要求对方恢复服务时，有没有说一个期限？现在过了多久？可以直接输入日期或大概天数。",
        ["说过期限，已经过了", "还没到说好的期限", "没有说过期限", "不清楚"],
        "只了解实际时间；过了自己提出的期限，不一定就能认定对方已超过合理等待时间。",
    ),
    "cond.performance_impossible": (
        "店关门后，对方还有办法提供你原来购买的服务吗？比如只是暂时停业，或能去其他门店使用。",
        ["已经无法继续履行", "仍能继续履行", "可以到其他门店继续使用", "不清楚"],
        "想先弄清服务是不是真的没法继续了，这会影响接下来怎么讨论退款。",
    ),
    "cond.alternative_performance": (
        "对方有没有提出转到其他门店使用？那个安排对你来说能用吗？",
        ["有可接受的替代服务", "没有可接受的替代服务", "不清楚"],
        "有转店安排不代表就不能退钱，还要看安排是否符合原先约定。",
    ),
    "cond.contract_exists": (
        "你和对方有没有约定过买什么服务、怎么收费？口头说好、聊天记录或办卡记录都可以说明。",
        ["已经成立", "没有成立", "不清楚"],
        "先弄清你们当时约定了什么，不要求一定有纸质合同。",
    ),
    "cond.payment_made": (
        "这笔钱你已经付给对方了吗？",
        ["已经支付", "没有支付", "不清楚"],
        "先确认是否付过钱，避免把未付款和要求退回已付款混在一起。",
    ),
    "cond.unperformed_balance": (
        "你付的钱里面，还有没用掉的余额或没上的课程吗？",
        ["还有未消费部分", "已经全部消费", "不清楚"],
        "需要分清已经用过和还没用过的部分，不能直接按全部付款算退款。",
    ),
    "cond.contract_terminated": (
        "你提出不再继续使用服务后，对方是什么回应？有没有双方同意结束的记录，或法院的处理结果？",
        ["双方已同意结束，或法院已确认", "我只提过退款，对方没同意", "还没明确说过", "不清楚"],
        "先了解你们沟通到哪一步；提出退款不一定等于双方已经结束约定。",
    ),
}

OPTION_STATUS: dict[tuple[str, str], ConditionStatus] = {
    ("cond.main_obligation_delayed", "时间已到，对方还没提供"): ConditionStatus.SATISFIED,
    ("cond.main_obligation_delayed", "已经按时提供"): ConditionStatus.NOT_SATISFIED,
    ("cond.main_obligation_delayed", "还没到说好的时间"): ConditionStatus.NOT_SATISFIED,
    ("cond.demand_delivered", "要求过，有对方收到的记录"): ConditionStatus.SATISFIED,
    ("cond.demand_delivered", "还没要求过"): ConditionStatus.NOT_SATISFIED,
    ("cond.demand_delivered", "要求过，但不确定对方收到"): ConditionStatus.UNKNOWN,
    ("cond.reasonable_period_expired", "说过期限，已经过了"): ConditionStatus.UNKNOWN,
    ("cond.reasonable_period_expired", "还没到说好的期限"): ConditionStatus.NOT_SATISFIED,
    ("cond.reasonable_period_expired", "没有说过期限"): ConditionStatus.UNKNOWN,
    # 沟通事实不能直接当成合同已经解除；保留原话供下一轮映射。
    ("cond.contract_terminated", "双方已同意结束，或法院已确认"): ConditionStatus.SATISFIED,
    ("cond.contract_terminated", "我只提过退款，对方没同意"): ConditionStatus.UNKNOWN,
    ("cond.contract_terminated", "还没明确说过"): ConditionStatus.UNKNOWN,
    ("cond.performance_impossible", "已经无法继续履行"): ConditionStatus.SATISFIED,
    ("cond.performance_impossible", "仍能继续履行"): ConditionStatus.NOT_SATISFIED,
    ("cond.performance_impossible", "可以到其他门店继续使用"): ConditionStatus.NOT_SATISFIED,
    ("cond.performance_impossible", "不清楚"): ConditionStatus.UNKNOWN,
    ("cond.alternative_performance", "有可接受的替代服务"): ConditionStatus.SATISFIED,
    ("cond.alternative_performance", "没有可接受的替代服务"): ConditionStatus.NOT_SATISFIED,
    ("cond.alternative_performance", "不清楚"): ConditionStatus.UNKNOWN,
    ("cond.contract_exists", "已经成立"): ConditionStatus.SATISFIED,
    ("cond.contract_exists", "没有成立"): ConditionStatus.NOT_SATISFIED,
    ("cond.contract_exists", "不清楚"): ConditionStatus.UNKNOWN,
    ("cond.payment_made", "已经支付"): ConditionStatus.SATISFIED,
    ("cond.payment_made", "没有支付"): ConditionStatus.NOT_SATISFIED,
    ("cond.payment_made", "不清楚"): ConditionStatus.UNKNOWN,
    ("cond.unperformed_balance", "还有未消费部分"): ConditionStatus.SATISFIED,
    ("cond.unperformed_balance", "已经全部消费"): ConditionStatus.NOT_SATISFIED,
    ("cond.unperformed_balance", "不清楚"): ConditionStatus.UNKNOWN,
    ("cond.contract_terminated", "已经解除"): ConditionStatus.SATISFIED,
    ("cond.contract_terminated", "尚未解除"): ConditionStatus.NOT_SATISFIED,
    ("cond.contract_terminated", "不清楚"): ConditionStatus.UNKNOWN,
}

DEFAULT_OPTION_STATUS: dict[str, ConditionStatus] = {
    "是": ConditionStatus.SATISFIED,
    "否": ConditionStatus.NOT_SATISFIED,
    "不清楚": ConditionStatus.UNKNOWN,
}


def option_status(condition_id: str, option: str) -> ConditionStatus | None:
    text = option.strip()
    if not text:
        return None
    mapped = OPTION_STATUS.get((condition_id, text))
    if mapped is not None:
        return mapped
    return DEFAULT_OPTION_STATUS.get(text)
