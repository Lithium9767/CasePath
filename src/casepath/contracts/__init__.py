# __init__.py 初始化成整个package包，方便import模块
# 在import contracts时会运行该py代码

# 可以用import contracts来代替 import contracts.api
# 或 from contracts import AnswerRequest, CapabilityStatus, ErrorResponse
from .api import AnswerRequest, CapabilityStatus, ErrorResponse
from .base import ContractModel, ScoreComponent, SourceSpan
from .case import (
    CaseIndexRecord,
    CaseRecord,
    ClaimRecord,
    ConditionFinding,
    CourtFinding,
    DecisionItem,
    ReasoningStep,
)
from .enums import (
    CapabilityMode,
    CaseRole,
    ConditionGroupOperator,
    ConditionGroupRole,
    ConditionStatus,
    DecisionStatus,
    ErrorCode,
    MaturityLevel,
    SessionStatus,
)
from .explanation import CitationRecord, EvidenceAction, ExplanationBranch, ExplanationPlan
from .query import (
    CandidateClaim,
    ConditionEvidence,
    DialogueTurn,
    QueryConditionState,
    QueryState,
    QuestionCandidate,
    UserFact,
)
from .retrieval import (
    ComparisonBundle,
    ConditionComparison,
    RetrievalBundle,
    RetrievalPath,
    ScoredReference,
)
from .rule import (
    ConditionGroup,
    LegalConsequence,
    ProvisionRef,
    RuleCondition,
    RuleException,
    RuleRecord,
)
from .session import AnswerInterpretation, CorrectionOperation, CreateSessionRequest
from .source import LegalSourceRecord, ProvisionRecord
from .workflow import WorkflowSnapshot

# 主要影响from contrast import * 的导入方式
# 如果__all__中没有列出，则from contrast import * 会报错
# 如果__all__中列出了，则from contrast import * 会导入__all__中列出的所有内容
__all__ = [
    "AnswerInterpretation",
    "AnswerRequest",
    "CandidateClaim",
    "CapabilityMode",
    "CapabilityStatus",
    "CaseIndexRecord",
    "CaseRecord",
    "CaseRole",
    "CitationRecord",
    "ClaimRecord",
    "ComparisonBundle",
    "ConditionComparison",
    "ConditionEvidence",
    "ConditionFinding",
    "ConditionGroup",
    "ConditionGroupOperator",
    "ConditionGroupRole",
    "ConditionStatus",
    "ContractModel",
    "CorrectionOperation",
    "CourtFinding",
    "CreateSessionRequest",
    "DecisionItem",
    "DecisionStatus",
    "DialogueTurn",
    "ErrorCode",
    "ErrorResponse",
    "EvidenceAction",
    "ExplanationBranch",
    "ExplanationPlan",
    "LegalConsequence",
    "LegalSourceRecord",
    "MaturityLevel",
    "ProvisionRecord",
    "ProvisionRef",
    "QueryConditionState",
    "QueryState",
    "QuestionCandidate",
    "ReasoningStep",
    "RetrievalBundle",
    "RetrievalPath",
    "RuleCondition",
    "RuleException",
    "RuleRecord",
    "ScoreComponent",
    "ScoredReference",
    "SessionStatus",
    "SourceSpan",
    "UserFact",
    "WorkflowSnapshot",
]
