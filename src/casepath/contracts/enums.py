from enum import StrEnum


class MaturityLevel(StrEnum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class ConditionStatus(StrEnum):
    SATISFIED = "SATISFIED"
    NOT_SATISFIED = "NOT_SATISFIED"
    UNKNOWN = "UNKNOWN"
    CONFLICTING = "CONFLICTING"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ConditionOperator(StrEnum):
    ALL = "ALL"
    ANY = "ANY"
    UNLESS = "UNLESS"
    THRESHOLD = "THRESHOLD"
    TEMPORAL = "TEMPORAL"
    REFERENCE = "REFERENCE"


class DecisionStatus(StrEnum):
    GRANTED = "GRANTED"
    PARTIALLY_GRANTED = "PARTIALLY_GRANTED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"
    UNKNOWN = "UNKNOWN"


class SessionStatus(StrEnum):
    INITIAL = "INITIAL"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    READY_TO_EXPLAIN = "READY_TO_EXPLAIN"
    COMPLETED = "COMPLETED"
    DEGRADED = "DEGRADED"


class CaseRole(StrEnum):
    SUPPORT = "SUPPORT"
    LIMITING = "LIMITING"
    BOUNDARY = "BOUNDARY"
    UNCERTAIN = "UNCERTAIN"
