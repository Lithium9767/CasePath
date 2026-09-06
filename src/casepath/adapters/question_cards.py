"""P4 实现区：追问卡片与选项到条件状态的显式映射。

选项按 `condition_id + 完整选项文本` 对应状态，不用通用肯定词套所有条件。
"""

from __future__ import annotations

from casepath.contracts import ConditionStatus

QUESTION_CARDS: dict[str, tuple[str, list[str], str]] = {
    "cond.performance_impossible": (
        "对方现在是已经无法继续提供约定服务，还是仍能继续履行？",
        ["已经无法继续履行", "仍能继续履行", "可以到其他门店继续使用", "不清楚"],
        "该事实会改变合同目的不能实现的解除路径是否成立。",
    ),
    "cond.alternative_performance": (
        "现在是否存在仍能实现合同目的的替代服务，例如转店？",
        ["有可接受的替代服务", "没有可接受的替代服务", "不清楚"],
        "替代履行只用于比较，不单独否定解除规则。",
    ),
    "cond.contract_exists": (
        "双方是否已经成立服务合同或会员协议？",
        ["已经成立", "没有成立", "不清楚"],
        "没有成立的合同关系时，解除与返还规则不适用。",
    ),
    "cond.payment_made": (
        "你是否已经支付了约定的服务费用？",
        ["已经支付", "没有支付", "不清楚"],
        "已付款是费用补救路径的核心事实之一。",
    ),
    "cond.unperformed_balance": (
        "是否还有尚未提供的服务或未消费余额？",
        ["还有未消费部分", "已经全部消费", "不清楚"],
        "未履行部分决定解除后的补救范围，不等于自动全额退款。",
    ),
    "cond.contract_terminated": (
        "合同是否已经通知解除或被确认解除？",
        ["已经解除", "尚未解除", "不清楚"],
        "解除效力发生后才进入恢复原状与补救规则。",
    ),
}

OPTION_STATUS: dict[tuple[str, str], ConditionStatus] = {
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
