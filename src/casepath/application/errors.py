"""应用错误不依赖 FastAPI，HTTP 状态码由交付层统一映射。"""


class SessionNotFound(Exception):
    """指定会话不存在。"""


class SessionConflict(Exception):
    """问题已变化、请求内容冲突或发生并发更新。"""


class InvalidAnswer(Exception):
    """回答为空或选项不属于当前问题。"""


class AnswerInterpreterUnavailable(Exception):
    """P4 回答解释能力未接入或调用失败，原状态保持不变。"""


class InvalidComponentOutput(Exception):
    """组件输出违反公共合同或证据引用约束。"""


class RetrieverUnavailable(Exception):
    """P4规则、案例或比较检索能力暂时不可用。"""


class GraphUnavailable(Exception):
    """Neo4j或其他法律图存储连接、超时或只读查询失败。"""


class LanguageModelUnavailable(Exception):
    """结构化LLM调用超时、失败或没有配置。"""
