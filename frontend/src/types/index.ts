// CasePath 前端数据契约
// 严格对齐仓库 src/casepath/contracts/ 的 Pydantic 模型（v1.1 / v1.2 / v1.3）。
// 字段名、枚举值、嵌套结构均与 P1 冻结的合同一致，不得自行增删字段。

// ---- 枚举 ----
export type MaturityLevel = 'L0' | 'L1' | 'L2' | 'L3';

export type ConditionStatus =
  | 'SATISFIED'
  | 'NOT_SATISFIED'
  | 'UNKNOWN'
  | 'CONFLICTING'
  | 'NOT_APPLICABLE';

export type ConditionGroupOperator = 'ALL' | 'ANY';

export type ConditionGroupRole = 'CORE' | 'DIAGNOSTIC';

export type DecisionStatus =
  | 'GRANTED'
  | 'PARTIALLY_GRANTED'
  | 'REJECTED'
  | 'WITHDRAWN'
  | 'UNKNOWN';

export type SessionStatus =
  | 'INITIAL'
  | 'NEEDS_CLARIFICATION'
  | 'READY_TO_EXPLAIN'
  | 'COMPLETED'
  | 'DEGRADED';

export type CaseRole = 'SUPPORT' | 'LIMITING' | 'BOUNDARY' | 'UNCERTAIN';

export type EvidenceRelation = 'SUPPORTS' | 'OPPOSES' | 'QUALIFIES';

export type CapabilityMode = 'LIVE' | 'DEMO' | 'MEMORY' | 'DISABLED';

export type ErrorCode =
  | 'CASEPATH_INVALID_REQUEST'
  | 'CASEPATH_SESSION_NOT_FOUND'
  | 'CASEPATH_CONTRACT_MISMATCH'
  | 'CASEPATH_RETRIEVER_UNAVAILABLE'
  | 'CASEPATH_GRAPH_UNAVAILABLE'
  | 'CASEPATH_CITATION_NOT_VERIFIED'
  | 'CASEPATH_INTERNAL_ERROR';

// ---- base.py ----
export interface SourceSpan {
  span_id: string;
  source_id: string;
  section?: string | null;
  paragraph_id?: string | null;
  start_offset: number;
  end_offset: number;
  quote: string;
}

export interface ScoreComponent {
  name: string;
  value: number;
  explanation: string;
}

// ---- rule.py ----
export interface ProvisionRef {
  source_id: string;
  provision_id: string;
  article_no: string;
  title: string;
  valid_from?: string | null;
  valid_to?: string | null;
}

export interface RuleCondition {
  condition_id: string;
  label: string;
  predicate: string;
  user_answerable: boolean;
  evidence_types: string[];
  source_span_ids: string[];
}

export interface ConditionGroup {
  group_id: string;
  label: string;
  role: ConditionGroupRole;
  operator: ConditionGroupOperator;
  member_condition_ids: string[];
}

export interface RuleException {
  exception_id: string;
  label: string;
  predicate: string;
  effect: string;
  source_span_ids: string[];
}

export interface LegalConsequence {
  consequence_id: string;
  consequence_type: string;
  description: string;
  source_span_ids: string[];
}

export interface RuleRecord {
  contract_version: string;
  rule_id: string;
  title: string;
  claim_types: string[];
  provisions: ProvisionRef[];
  conditions: RuleCondition[];
  condition_groups: ConditionGroup[];
  exceptions: RuleException[];
  consequences: LegalConsequence[];
  maturity: MaturityLevel;
  source_spans: SourceSpan[];
}

// ---- case.py ----
export interface ClaimRecord {
  claim_id: string;
  claim_type: string;
  claimant?: string | null;
  respondent?: string | null;
  requested_remedy: string;
  amount?: number | null;
  invoked_rule_ids: string[];
}

export interface CourtFinding {
  finding_id: string;
  predicate: string;
  polarity?: boolean | null;
  source_span_ids: string[];
}

export interface ConditionFinding {
  condition_id: string;
  status: ConditionStatus;
  finding_ids: string[];
  confidence: number;
  source_span_ids: string[];
  human_verified: boolean;
}

export interface ReasoningStep {
  reasoning_id: string;
  premise_finding_ids: string[];
  applied_rule_ids: string[];
  conclusion: string;
  source_span_ids: string[];
}

export interface DecisionItem {
  decision_id: string;
  claim_id: string;
  status: DecisionStatus;
  description: string;
  amount?: number | null;
  source_span_ids: string[];
}

export interface CaseRecord {
  contract_version: string;
  case_id: string;
  title: string;
  case_no?: string | null;
  court?: string | null;
  judgment_date?: string | null;
  cause?: string | null;
  maturity: MaturityLevel;
  claims: ClaimRecord[];
  findings: CourtFinding[];
  condition_findings: ConditionFinding[];
  reasoning_steps: ReasoningStep[];
  decisions: DecisionItem[];
  source_spans: SourceSpan[];
}

// ---- query.py ----
export interface UserFact {
  fact_id: string;
  text: string;
  predicate?: string | null;
  value?: string | boolean | number | null;
  source_turn: number;
}

export interface CandidateClaim {
  claim_type: string;
  requested_remedy: string;
  confidence: number;
}

export interface ConditionEvidence {
  fact_id: string;
  relation: EvidenceRelation;
  confidence: number;
  reason: string;
}

export interface QueryConditionState {
  condition_id: string;
  status: ConditionStatus;
  supporting_fact_ids: string[];
  confidence?: number | null;
  evidence: ConditionEvidence[];
  mapping_reasons: string[];
  score_components: ScoreComponent[];
  last_updated_turn: number;
}

export interface DialogueTurn {
  turn_id: number;
  question_id: string;
  condition_id: string;
  question: string;
  answer?: string | null;
}

export interface QueryState {
  contract_version: string;
  session_id: string;
  initial_query: string;
  status: SessionStatus;
  user_facts: UserFact[];
  candidate_claims: CandidateClaim[];
  condition_states: QueryConditionState[];
  dialogue_history: DialogueTurn[];
  created_at: string;
}

export interface QuestionCandidate {
  question_id: string;
  condition_id: string;
  question: string;
  why_asked: string;
  options: string[];
  utility: number;
  score_components: ScoreComponent[];
  supporting_case_ids: string[];
  limiting_case_ids: string[];
  boundary_case_ids: string[];
}

// ---- retrieval.py ----
export interface RetrievalPath {
  node_ids: string[];
  edge_types: string[];
  score: number;
  source_span_ids: string[];
}

export interface ScoredReference {
  object_id: string;
  score: number;
  reasons: string[];
  score_components: ScoreComponent[];
  retrieval_channels: string[];
  source_span_ids: string[];
  graph_paths: RetrievalPath[];
}

export interface RetrievalBundle {
  contract_version: string;
  rule_refs: ScoredReference[];
  support_case_refs: ScoredReference[];
  limiting_case_refs: ScoredReference[];
  boundary_case_refs: ScoredReference[];
  cited_span_ids: string[];
  degraded: boolean;
  degradation_reason?: string | null;
  fused_case_count?: number;
  undetermined_case_count?: number;
  undetermined_case_ids?: string[];
  undetermined_reason_summary?: string[];
}

export interface ConditionComparison {
  condition_id: string;
  status_counts: Partial<Record<ConditionStatus, number>>;
  condition_entropy: number;
  outcome_mutual_information: number;
  expected_information_gain: number;
  expected_rank_change: number;
  expected_branch_reduction: number;
  case_disagreement_score: number;
  rule_condition_discriminativeness: number;
  support_case_ids: string[];
  limiting_case_ids: string[];
  boundary_case_ids: string[];
  score_components: ScoreComponent[];
}

export interface ComparisonBundle {
  contract_version: string;
  condition_comparisons: ConditionComparison[];
  ranked_condition_ids: string[];
  degraded: boolean;
  degradation_reason?: string | null;
}

// ---- explanation.py ----
export interface CitationRecord {
  citation_id: string;
  source_span_ids: string[];
  supports: string;
  verified: boolean;
}

export interface ExplanationBranch {
  branch_id: string;
  condition: string;
  explanation: string;
  citation_ids: string[];
}

export interface EvidenceAction {
  action_id: string;
  description: string;
  related_condition_ids: string[];
}

export interface ExplanationPlan {
  contract_version: string;
  session_id: string;
  main_explanation: string;
  candidate_claims: string[];
  applicable_rule_ids: string[];
  support_case_ids: string[];
  limiting_case_ids: string[];
  boundary_case_ids: string[];
  conditional_branches: ExplanationBranch[];
  unresolved_condition_ids: string[];
  evidence_actions: EvidenceAction[];
  citations: CitationRecord[];
  disclaimer: string;
  fused_case_count?: number;
  undetermined_case_count?: number;
  role_assigned_case_count?: number;
  undetermined_reason_summary?: string[];
  question_stop_reason?: string | null;
}

// ---- workflow.py（P1 API 返回结构） ----
export interface WorkflowSnapshot {
  contract_version: string;
  query_state: QueryState;
  retrieval_bundle: RetrievalBundle;
  comparison_bundle: ComparisonBundle | null;
  next_question: QuestionCandidate | null;
  explanation_plan: ExplanationPlan;
  trace: string[];
}

// ---- api.py / session.py ----
export interface CreateSessionRequest {
  contract_version: string;
  query: string;
}

export interface AnswerRequest {
  contract_version: string;
  question_id: string;
  condition_id: string;
  answer: string;
  selected_option?: string | null;
}

export interface AnswerInterpretation {
  contract_version: string;
  new_facts: UserFact[];
  condition_updates: QueryConditionState[];
  disposition?: 'FACTS_PROVIDED' | 'UNABLE' | 'DECLINED' | 'MIXED';
  unable_condition_ids?: string[];
  declined_condition_ids?: string[];
  corrections?: Array<{
    target_fact_id: string;
    replacement_fact_id?: string | null;
    condition_id?: string | null;
    reason: string;
  }>;
}

export interface ErrorResponse {
  contract_version: string;
  code: ErrorCode;
  message: string;
  details: Record<string, string | number | boolean | null>;
  retryable: boolean;
  request_id?: string | null;
}

export interface CapabilityStatus {
  contract_version: string;
  capability: string;
  available: boolean;
  mode: CapabilityMode;
  degraded: boolean;
  reason?: string | null;
}

// 前端内部解析视图：在 WorkflowSnapshot 基础上附加按 ID 解析的完整记录，
// 便于离线演示时直接渲染详情（真实 API 模式下这些映射可为空）。
export interface ResolvedSnapshot extends WorkflowSnapshot {
  rules: Record<string, RuleRecord>;
  cases: Record<string, CaseRecord>;
  spans: Record<string, SourceSpan>;
}

// ---- 会话展示用消息 ----
export interface Understanding {
  candidate_claims: CandidateClaim[];
  condition_states: QueryConditionState[];
  condition_labels: Record<string, string>;
}

export type ChatMessage =
  | { id: string; role: 'user'; text: string; timestamp: number }
  | { id: string; role: 'assistant'; kind: 'understanding'; understanding: Understanding; timestamp: number }
  | { id: string; role: 'assistant'; kind: 'question'; question: QuestionCandidate; timestamp: number }
  | { id: string; role: 'assistant'; kind: 'explanation'; explanation: ExplanationPlan; timestamp: number }
  | { id: string; role: 'assistant'; kind: 'text'; text: string; timestamp: number };
