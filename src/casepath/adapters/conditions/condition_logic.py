"""P4 公共条件组语义：未知不是否定，ANY 的成员差异不是组冲突。"""

from casepath.contracts import ConditionStatus


def group_truth(operator: str, statuses: list[ConditionStatus]) -> bool | None:
    """只返回有充分条件证据的真/假；缺失、冲突和不适用均保留未知。"""
    if not statuses:
        return None
    if operator == "ALL":
        if ConditionStatus.NOT_SATISFIED in statuses:
            return False
        if all(item is ConditionStatus.SATISFIED for item in statuses):
            return True
    elif operator == "ANY":
        if ConditionStatus.SATISFIED in statuses:
            return True
        if all(item is ConditionStatus.NOT_SATISFIED for item in statuses):
            return False
    return None
