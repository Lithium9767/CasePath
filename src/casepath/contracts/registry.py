"""导出工具与 HTTP Schema 查询共用注册表，避免新增模型时遗漏一端。"""

from . import (
    AnswerInterpretation,
    AnswerRequest,
    CapabilityStatus,
    CaseRecord,
    CreateSessionRequest,
    ErrorResponse,
    ExplanationPlan,
    LegalSourceRecord,
    ProvisionRecord,
    QueryState,
    RetrievalBundle,
    RuleRecord,
    WorkflowSnapshot,
)

CONTRACTS = {
    "legal-source-record": LegalSourceRecord,
    "provision-record": ProvisionRecord,
    "rule-record": RuleRecord,
    "case-record": CaseRecord,
    "query-state": QueryState,
    "retrieval-bundle": RetrievalBundle,
    "explanation-plan": ExplanationPlan,
    "answer-request": AnswerRequest,
    "error-response": ErrorResponse,
    "capability-status": CapabilityStatus,
    "workflow-snapshot": WorkflowSnapshot,
    "create-session-request": CreateSessionRequest,
    "answer-interpretation": AnswerInterpretation,
}
