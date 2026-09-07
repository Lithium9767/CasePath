import type { ConditionStatus, DecisionStatus, SessionStatus } from '@/types';

export const STATUS_LABEL: Record<ConditionStatus, string> = {
  SATISFIED: '满足',
  NOT_SATISFIED: '不满足',
  UNKNOWN: '未知',
  CONFLICTING: '冲突',
  NOT_APPLICABLE: '不适用',
};

export const STATUS_COLOR: Record<ConditionStatus, string> = {
  SATISFIED: 'success',
  NOT_SATISFIED: 'error',
  UNKNOWN: 'warning',
  CONFLICTING: 'purple',
  NOT_APPLICABLE: 'default',
};

export const DECISION_LABEL: Record<DecisionStatus, string> = {
  GRANTED: '支持',
  PARTIALLY_GRANTED: '部分支持',
  REJECTED: '驳回',
  WITHDRAWN: '撤回',
  UNKNOWN: '未知',
};

export const DECISION_COLOR: Record<DecisionStatus, string> = {
  GRANTED: 'success',
  PARTIALLY_GRANTED: 'warning',
  REJECTED: 'error',
  WITHDRAWN: 'default',
  UNKNOWN: 'default',
};

export const SESSION_LABEL: Record<SessionStatus, string> = {
  INITIAL: '初始',
  NEEDS_CLARIFICATION: '待追问',
  READY_TO_EXPLAIN: '可解释',
  COMPLETED: '已完成',
  DEGRADED: '已降级',
};

export const SESSION_COLOR: Record<SessionStatus, string> = {
  INITIAL: 'default',
  NEEDS_CLARIFICATION: 'processing',
  READY_TO_EXPLAIN: 'success',
  COMPLETED: 'success',
  DEGRADED: 'warning',
};

export const formatScore = (v: number): string => v.toFixed(2);
