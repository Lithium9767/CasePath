"""P4 公共条件组语义：未知不是否定，ANY 的成员差异不是组冲突。"""

from casepath.contracts import ConditionStatus


def group_truth(operator: str, statuses: list[ConditionStatus]) -> bool | None:
    """只返回有充分条件证据的真/假；缺失、冲突和不适用均保留未知。"""
    if not statuses:
        # 空组返回未知，不使用 Python all([]) 为真的数学约定替缺失证据下结论。
        return None
    if operator == "ALL":
        # ALL：一项明确否定即可判假；只有全部明确满足才判真，其余未知。
        if ConditionStatus.NOT_SATISFIED in statuses:
            return False
        if all(item is ConditionStatus.SATISFIED for item in statuses):
            return True
    elif operator == "ANY":
        # ANY：一项明确满足即可判真；只有全部明确否定才判假，其余未知。
        if ConditionStatus.SATISFIED in statuses:
            return True
        if all(item is ConditionStatus.NOT_SATISFIED for item in statuses):
            return False
    return None
