// Mock 状态机：模拟 P1 后端 WorkflowSnapshot 的确定性编排。
// 返回 ResolvedSnapshot（v1.3 WorkflowSnapshot + 按 ID 解析的 rules/cases/spans）。

import type {
  ComparisonBundle,
  ConditionStatus,
  ExplanationPlan,
  QueryConditionState,
  QueryState,
  QuestionCandidate,
  ResolvedSnapshot,
  RetrievalBundle,
} from '@/types';
import {
  CASES,
  DISCLAIMER,
  LIMITING_CASE,
  PRESET_QUERY,
  RULE,
  RULES,
  SPANS,
  SUPPORT_CASE,
} from '@/mock/scenario';

const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

const TRACE = [
  'PARSE_QUERY',
  'RETRIEVE_RULES',
  'RETRIEVE_CASES',
  'BUILD_CONTRAST_PANEL',
  'PROJECT_QUERY',
  'SCORE_QUESTIONS',
  'BUILD_EXPLANATION_PLAN',
  'VERIFY_CITATIONS',
];

const nextSessionId = (): string =>
  `demo-session-${Math.random().toString(16).slice(2, 10)}`;

const satisfied = (
  conditionId: string,
  factId: string,
  confidence: number,
): QueryConditionState => ({
  condition_id: conditionId,
  status: 'SATISFIED',
  supporting_fact_ids: [factId],
  confidence,
  evidence: [
    {
      fact_id: factId,
      relation: 'SUPPORTS',
      confidence,
      reason: '用户初始描述或后续回答中体现该条件',
    },
  ],
  mapping_reasons: [`依据事实 ${factId} 判定为满足`],
  score_components: [{ name: 'structured_llm', value: confidence, explanation: '结构化事实映射置信度' }],
  last_updated_turn: 0,
});

const unknown = (conditionId: string): QueryConditionState => ({
  condition_id: conditionId,
  status: 'UNKNOWN',
  supporting_fact_ids: [],
  confidence: null,
  evidence: [],
  mapping_reasons: ['用户未提供相关信息，保持未知'],
  score_components: [],
  last_updated_turn: 0,
});

function initialQueryState(sessionId: string, query: string): QueryState {
  return {
    contract_version: '1.3',
    session_id: sessionId,
    initial_query: query,
    status: 'NEEDS_CLARIFICATION',
    user_facts: [{ fact_id: 'fact.1', text: query, predicate: null, value: null, source_turn: 0 }],
    candidate_claims: [
      { claim_type: '服务合同解除与返还', requested_remedy: '解除合同并返还未消费余额', confidence: 0.82 },
    ],
    condition_states: [
      satisfied('cond.contract_exists', 'fact.1', 0.95),
      satisfied('cond.payment_made', 'fact.1', 0.95),
      satisfied('cond.obligation_unperformed', 'fact.1', 0.9),
      unknown('cond.performance_impossible'),
      unknown('cond.alternative_performance'),
    ],
    dialogue_history: [],
    created_at: new Date().toISOString(),
  };
}

function retrievalBundle(): RetrievalBundle {
  return {
    contract_version: '1.3',
    rule_refs: [
      {
        object_id: RULE.rule_id,
        score: 0.91,
        reasons: ['服务合同', '返还未消费余额'],
        score_components: [{ name: 'bm25', value: 0.87, explanation: '关键词匹配' }],
        retrieval_channels: ['bm25'],
        source_span_ids: ['span.civil-code.563'],
        graph_paths: [],
      },
    ],
    support_case_refs: [
      {
        object_id: SUPPORT_CASE.case_id,
        score: 0.88,
        reasons: ['经营场所关闭', '合同解除'],
        score_components: [],
        retrieval_channels: ['bm25'],
        source_span_ids: ['span.case.zeng.reasoning'],
        graph_paths: [],
      },
    ],
    limiting_case_refs: [
      {
        object_id: LIMITING_CASE.case_id,
        score: 0.82,
        reasons: ['仍可继续履行', '个人原因不再消费'],
        score_components: [],
        retrieval_channels: ['bm25'],
        source_span_ids: ['span.case.wang.reasoning'],
        graph_paths: [],
      },
    ],
    boundary_case_refs: [],
    cited_span_ids: [
      'span.civil-code.563',
      'span.civil-code.566',
      'span.case.zeng.reasoning',
      'span.case.wang.reasoning',
    ],
    degraded: false,
    degradation_reason: null,
  };
}

function comparisonBundle(): ComparisonBundle {
  return {
    contract_version: '1.3',
    condition_comparisons: [
      {
        condition_id: 'cond.performance_impossible',
        status_counts: { SATISFIED: 1, NOT_SATISFIED: 1 },
        condition_entropy: 0.92,
        outcome_mutual_information: 0.61,
        expected_information_gain: 0.74,
        expected_rank_change: 0.55,
        expected_branch_reduction: 0.67,
        case_disagreement_score: 0.83,
        rule_condition_discriminativeness: 0.79,
        support_case_ids: [SUPPORT_CASE.case_id],
        limiting_case_ids: [LIMITING_CASE.case_id],
        boundary_case_ids: [],
        score_components: [
          { name: 'information_gain', value: 0.74, explanation: '回答该条件预计减少候选解释分支' },
        ],
      },
      {
        condition_id: 'cond.alternative_performance',
        status_counts: { SATISFIED: 1, NOT_SATISFIED: 1 },
        condition_entropy: 0.7,
        outcome_mutual_information: 0.48,
        expected_information_gain: 0.52,
        expected_rank_change: 0.4,
        expected_branch_reduction: 0.5,
        case_disagreement_score: 0.66,
        rule_condition_discriminativeness: 0.6,
        support_case_ids: [SUPPORT_CASE.case_id],
        limiting_case_ids: [LIMITING_CASE.case_id],
        boundary_case_ids: [],
        score_components: [],
      },
    ],
    ranked_condition_ids: ['cond.performance_impossible', 'cond.alternative_performance'],
    degraded: false,
    degradation_reason: null,
  };
}

function question(): QuestionCandidate {
  return {
    question_id: 'question.performance_impossible.1',
    condition_id: 'cond.performance_impossible',
    question: '健身房是永久停业，还是暂时关闭后仍可能恢复营业？合同或老板是否安排你到其他门店继续使用？',
    why_asked: '是否已经无法继续履行，会改变解除和返还规则的解释分支。',
    options: ['永久停止经营，无替代门店', '暂时停业，之后可恢复', '安排了其他门店继续使用', '暂时不知道'],
    utility: 0.87,
    score_components: [
      { name: 'rule_centrality', value: 0.9, explanation: '该条件属于解除与返还的 CORE 条件组' },
      { name: 'case_contrast', value: 0.83, explanation: '支持与限制案例在该条件上状态相反' },
      { name: 'answerability', value: 0.8, explanation: '普通用户可以直接回答' },
      { name: 'evidenceability', value: 0.7, explanation: '停业事实可用照片、公告等证据支持' },
      { name: 'interaction_cost', value: -0.15, explanation: '回答成本低' },
    ],
    supporting_case_ids: [SUPPORT_CASE.case_id],
    limiting_case_ids: [LIMITING_CASE.case_id],
    boundary_case_ids: [],
  };
}

function initialExplanationPlan(sessionId: string): ExplanationPlan {
  return {
    contract_version: '1.3',
    session_id: sessionId,
    main_explanation: '仍需确认经营者是否已经无法继续履行。',
    candidate_claims: ['服务合同解除与返还'],
    applicable_rule_ids: [RULE.rule_id],
    support_case_ids: [SUPPORT_CASE.case_id],
    limiting_case_ids: [LIMITING_CASE.case_id],
    boundary_case_ids: [],
    conditional_branches: [],
    unresolved_condition_ids: ['cond.performance_impossible', 'cond.alternative_performance'],
    evidence_actions: [],
    citations: [
      {
        citation_id: 'citation.civil-code.563',
        source_span_ids: ['span.civil-code.563'],
        supports: '合同解除规则候选依据',
        verified: false,
      },
    ],
    disclaimer: DISCLAIMER,
  };
}

function finalExplanationPlan(
  sessionId: string,
  performanceStatus: ConditionStatus,
  alternativeStatus: ConditionStatus,
): ExplanationPlan {
  let main: string;
  if (performanceStatus === 'SATISFIED' && alternativeStatus === 'NOT_SATISFIED') {
    main =
      '根据你补充的“永久停业且没有替代门店”，当前事实更支持经营者已经无法继续履行服务。' +
      '支持案例中，法院将“停止经营、剩余服务无法提供”作为支持解除合同并返还余额的重要事实；' +
      '限制性案例未支持相同请求的关键区别是经营者仍可继续提供约定服务。' +
      '因此，你目前可以重点考虑“解除服务合同并请求返还未消费余额”这一解释路径。';
  } else if (performanceStatus === 'NOT_SATISFIED') {
    main =
      '根据你补充的“暂时停业、之后可恢复”，当前事实尚不足以认定经营者无法继续履行。' +
      '此时解除合同并获得退款的支持程度较低，建议进一步确认停业持续时间与是否影响合同目的实现。';
  } else {
    main =
      '当前仍缺少关键事实，无法稳定判断合同目的是否不能实现。' +
      '请补充停业情况、是否安排替代门店及合同退款条款后，再判断解除与返还的解释路径。';
  }

  return {
    contract_version: '1.3',
    session_id: sessionId,
    main_explanation: main,
    candidate_claims: ['服务合同解除与返还'],
    applicable_rule_ids: [RULE.rule_id],
    support_case_ids: [SUPPORT_CASE.case_id],
    limiting_case_ids: [LIMITING_CASE.case_id],
    boundary_case_ids: [],
    conditional_branches: [
      {
        branch_id: 'branch.performance_impossible',
        condition: '经营者是否无法继续履行',
        explanation:
          '若经营者永久停业且无替代履行，倾向于支持解除并返还余额；若仍可继续履行，则倾向于不支持。',
        citation_ids: ['citation.civil-code.563', 'citation.case.zeng', 'citation.case.wang'],
      },
    ],
    unresolved_condition_ids: [],
    evidence_actions: [
      { action_id: 'action.contract', description: '保存会员服务协议，证明合同关系与退款条款', related_condition_ids: ['cond.contract_exists'] },
      { action_id: 'action.payment', description: '保存付款凭证（转账/收据），证明已支付预付款', related_condition_ids: ['cond.payment_made'] },
      { action_id: 'action.balance', description: '保存剩余余额证明（消费记录/会员系统截图）', related_condition_ids: ['cond.obligation_unperformed'] },
      { action_id: 'action.closure', description: '保存门店停业证据（现场照片/公告/聊天记录）', related_condition_ids: ['cond.performance_impossible'] },
    ],
    citations: [
      { citation_id: 'citation.civil-code.563', source_span_ids: ['span.civil-code.563'], supports: '合同解除规则依据', verified: true },
      { citation_id: 'citation.case.zeng', source_span_ids: ['span.case.zeng.reasoning'], supports: '支持案例的裁判理由', verified: true },
      { citation_id: 'citation.case.wang', source_span_ids: ['span.case.wang.reasoning'], supports: '限制案例的裁判理由', verified: true },
    ],
    disclaimer: DISCLAIMER,
  };
}

const sessions = new Map<string, ResolvedSnapshot>();

const isGymQuery = (query: string): boolean =>
  /健身房|健身|会员|预付款|预付费|退款|充了|关门|停业/.test(query);

export class MockEngine {
  analyze(query: string): ResolvedSnapshot {
    const sessionId = nextSessionId();
    const text = (query || '').trim() || PRESET_QUERY;

    if (!isGymQuery(text)) {
      const plan: ExplanationPlan = {
        ...initialExplanationPlan(sessionId),
        main_explanation:
          '当前离线演示数据仅覆盖“服务合同解除与返还”场景，未识别到可处理的其他民事问题。请尝试健身房预付费退款相关问题。',
      };
      const snapshot: ResolvedSnapshot = {
        contract_version: '1.3',
        query_state: { ...initialQueryState(sessionId, text), status: 'DEGRADED' },
        retrieval_bundle: retrievalBundle(),
        comparison_bundle: comparisonBundle(),
        next_question: null,
        explanation_plan: plan,
        trace: ['PARSE_QUERY', 'DEGRADE_TO_GENERAL_INFO'],
        rules: RULES,
        cases: CASES,
        spans: SPANS,
      };
      sessions.set(sessionId, snapshot);
      return clone(snapshot);
    }

    const snapshot: ResolvedSnapshot = {
      contract_version: '1.3',
      query_state: initialQueryState(sessionId, text),
      retrieval_bundle: retrievalBundle(),
      comparison_bundle: comparisonBundle(),
      next_question: question(),
      explanation_plan: initialExplanationPlan(sessionId),
      trace: [...TRACE],
      rules: RULES,
      cases: CASES,
      spans: SPANS,
    };
    sessions.set(sessionId, snapshot);
    return clone(snapshot);
  }

  answer(sessionId: string, conditionId: string, answer: string): ResolvedSnapshot {
    const prev = sessions.get(sessionId);
    const base = prev ?? this.analyze(answer);

    let performanceStatus: ConditionStatus = 'UNKNOWN';
    let alternativeStatus: ConditionStatus = 'UNKNOWN';
    if (answer.includes('永久') || answer.includes('无替代')) {
      performanceStatus = 'SATISFIED';
      alternativeStatus = 'NOT_SATISFIED';
    } else if (answer.includes('暂时')) {
      performanceStatus = 'NOT_SATISFIED';
    } else if (answer.includes('其他门店') || answer.includes('安排')) {
      alternativeStatus = 'SATISFIED';
    } else {
      performanceStatus = 'SATISFIED';
      alternativeStatus = 'NOT_SATISFIED';
    }

    const turnId = base.query_state.dialogue_history.length + 1;
    const factId = `fact.answer.${turnId}`;

    const updated = (cid: string, status: ConditionStatus): QueryConditionState => ({
      condition_id: cid,
      status,
      supporting_fact_ids: status === 'UNKNOWN' ? [] : [factId],
      confidence: status === 'UNKNOWN' ? null : 0.9,
      evidence:
        status === 'UNKNOWN'
          ? []
          : [{ fact_id: factId, relation: 'SUPPORTS', confidence: 0.9, reason: '用户追问回答中体现该条件' }],
      mapping_reasons: [`依据事实 ${factId} 判定为 ${status}`],
      score_components: [{ name: 'structured_llm', value: 0.9, explanation: '回答解释置信度' }],
      last_updated_turn: turnId,
    });

    const states = new Map(base.query_state.condition_states.map((s) => [s.condition_id, s]));
    states.set(conditionId, updated(conditionId, performanceStatus));
    states.set('cond.alternative_performance', updated('cond.alternative_performance', alternativeStatus));

    const order = [
      'cond.contract_exists',
      'cond.payment_made',
      'cond.obligation_unperformed',
      'cond.performance_impossible',
      'cond.alternative_performance',
    ];

    const queryState: QueryState = {
      ...base.query_state,
      status: 'READY_TO_EXPLAIN',
      user_facts: [
        ...base.query_state.user_facts,
        { fact_id: factId, text: answer, predicate: null, value: null, source_turn: turnId },
      ],
      condition_states: order.map((id) => states.get(id)!),
      dialogue_history: [
        ...base.query_state.dialogue_history,
        {
          turn_id: turnId,
          question_id: 'question.performance_impossible.1',
          condition_id: conditionId,
          question: '健身房是永久停业，还是暂时关闭后仍可能恢复营业？',
          answer,
        },
      ],
    };

    const snapshot: ResolvedSnapshot = {
      contract_version: '1.3',
      query_state: queryState,
      retrieval_bundle: base.retrieval_bundle,
      comparison_bundle: base.comparison_bundle,
      next_question: null,
      explanation_plan: finalExplanationPlan(sessionId, performanceStatus, alternativeStatus),
      trace: [...base.trace, 'UPDATE_STATE'],
      rules: RULES,
      cases: CASES,
      spans: SPANS,
    };
    sessions.set(sessionId, snapshot);
    return clone(snapshot);
  }

  skip(sessionId: string): ResolvedSnapshot {
    const prev = sessions.get(sessionId);
    if (!prev) return this.analyze('');
    const snapshot: ResolvedSnapshot = { ...clone(prev), next_question: null };
    sessions.set(sessionId, snapshot);
    return clone(snapshot);
  }
}

export const mockEngine = new MockEngine();
