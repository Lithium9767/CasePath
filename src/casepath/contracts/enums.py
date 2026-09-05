from enum import StrEnum


class MaturityLevel(StrEnum): # 数据成熟度
    L0 = "L0" # L0：只有原始数据，例如法条已经导入但还没有拆成：成立条件，例外条件，法律后果
    L1 = "L1" # L1：机器初步抽取，但未人工核验还没有确认
    L2 = "L2" # L2：经过抽检和校正，输出时仍然要显示成熟度警告
    L3 = "L3" # L3：深度人工复核


class ConditionStatus(StrEnum): # 规则条件状态
    SATISFIED = "SATISFIED" # 满足
    NOT_SATISFIED = "NOT_SATISFIED" # 不满足
    UNKNOWN = "UNKNOWN" # 未知
    CONFLICTING = "CONFLICTING" # 冲突
    NOT_APPLICABLE = "NOT_APPLICABLE" # 不适用


class ConditionGroupOperator(StrEnum): # 条件组怎样组合，这个枚举用于描述组内多个条件之间如何计算。
    ALL = "ALL" # 所有条件都满足，表示且
    ANY = "ANY" # 只要有一个条件满足，表示或


# 以下操作符保留为后续设计备忘，本周MVP不开放为可执行枚举值：
# UNLESS = "UNLESS" # 除非存在某个例外
# THRESHOLD = "THRESHOLD" # 达到一定数量或程度
# TEMPORAL = "TEMPORAL" # 表示条件之间存在先后顺序或期限要求。
# REFERENCE = "REFERENCE" # 引用其他条件或规则,表示当前条件需要引用另一个已经定义的条件。


# ConditionOperator目前存在的问题
# 当前代码把它放在单个 RuleCondition 上：
# class RuleCondition:
#     operator: ConditionOperator = ConditionOperator.ALL
# 但 ALL、ANY通常描述的是“一组条件如何组合”，不是单个原子条件。
# 例如：
# 经营者已经停止经营
# 这是一个原子条件，单独给它写 ALL没有明确意义。
# 更合理的长期结构是：
# class RuleCondition:
#     condition_id: Identifier
#     predicate: str

# class ConditionGroup:
#     group_id: Identifier
#     operator: ALL | ANY
#     member_condition_ids: list[Identifier]
# 例如：
# {
#   "group_id": "group.termination.requirements",
#   "operator": "ALL",
#   "member_condition_ids": [
#     "cond.contract.exists",
#     "cond.payment.made",
#     "cond.performance.impossible"
#   ]
# }
# 对一周MVP，我建议：
# - 暂时只实际使用 ALL；
# - ANY在确有组合条件时使用；
# - 例外继续使用 RuleException；
# - UNLESS、THRESHOLD、TEMPORAL、REFERENCE暂时不要进入计算；
# - 后续增加 ConditionGroup 后再正式启用复杂操作符。

class DecisionStatus(StrEnum):  # 法院对某项请求的处理结果
    GRANTED = "GRANTED" # 支持
    PARTIALLY_GRANTED = "PARTIALLY_GRANTED" # 部分支持
    REJECTED = "REJECTED" # 不支持
    WITHDRAWN = "WITHDRAWN" # 撤回
    UNKNOWN = "UNKNOWN" # 未知


class SessionStatus(StrEnum): #用户咨询进行到哪一步,会话状态
    INITIAL = "INITIAL" # 初始状态，用户刚刚提交问题，会话已经创建，但还没有完成第一次分析。
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION" # 需要追问，系统发现存在高价值的未知条件，并且已经生成下一条问题
    READY_TO_EXPLAIN = "READY_TO_EXPLAIN" # 可以生成解释，表示出现以下任一种情况：
    # - 关键条件已经足够明确；
    # - 没有更高价值的问题；
    # - 达到最大追问轮数；
    # - 用户选择不再回答，直接查看当前解释。
    COMPLETED = "COMPLETED" # 完成
    DEGRADED = "DEGRADED" # 降级，DEGRADED：降级运行，表示系统仍然返回结果，但某些必要能力不可用。


class CaseRole(StrEnum): # 某个案例在当前比较中的角色
    SUPPORT = "SUPPORT" #支 持案例，表示该案例的规则条件和裁判路径，更支持当前候选解释。
    LIMITING = "LIMITING" # 限制案例，表示该案例说明当前解释存在限制条件，提示系统找出案例差异
    BOUNDARY = "BOUNDARY" # 边界案例，表示案例处在规则适用的边界位置。
    UNCERTAIN = "UNCERTAIN" # 不确定案例，信息不足


class ErrorCode(StrEnum): # 正式API允许返回的稳定机器错误码
    INVALID_REQUEST = "CASEPATH_INVALID_REQUEST" # 请求字段缺失、类型错误或内容不合法
    SESSION_NOT_FOUND = "CASEPATH_SESSION_NOT_FOUND" # 找不到指定用户会话
    CONTRACT_MISMATCH = "CASEPATH_CONTRACT_MISMATCH" # 请求使用了不兼容的合同版本
    RETRIEVER_UNAVAILABLE = "CASEPATH_RETRIEVER_UNAVAILABLE" # 规则或案例检索器不可用
    GRAPH_UNAVAILABLE = "CASEPATH_GRAPH_UNAVAILABLE" # Neo4j或其他图存储不可用
    CITATION_NOT_VERIFIED = "CASEPATH_CITATION_NOT_VERIFIED" # 法条或案例引用未通过核验
    INTERNAL_ERROR = "CASEPATH_INTERNAL_ERROR" # 未被其他错误码覆盖的服务端错误


class CapabilityMode(StrEnum): # 系统能力当前采用的运行方式
    LIVE = "LIVE" # 使用真实数据和正式组件
    DEMO = "DEMO" # 使用固定演示数据或演示算法
    MEMORY = "MEMORY" # 使用内存Repository作为持久化组件的替代
    DISABLED = "DISABLED" # 该能力未启用
