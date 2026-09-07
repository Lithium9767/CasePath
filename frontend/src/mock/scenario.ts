// 预置演示数据：严格对齐 src/casepath/contracts 冻结合同（v1.1/v1.3）的字段名。
// 仅用于前端离线演示，不构成正式法律内容。

import type { CaseRecord, RuleRecord, SourceSpan } from '@/types';

// ---- 原文跨度 SourceSpan ----
export const SPANS: Record<string, SourceSpan> = {
  'span.civil-code.563': {
    span_id: 'span.civil-code.563',
    source_id: 'law.prc.civil_code.2021',
    section: '第五百六十三条',
    paragraph_id: 'article-563',
    start_offset: 0,
    end_offset: 1,
    quote:
      '有下列情形之一的，当事人可以解除合同：……（四）当事人一方迟延履行债务或者有其他违约行为致使不能实现合同目的……',
  },
  'span.civil-code.566': {
    span_id: 'span.civil-code.566',
    source_id: 'law.prc.civil_code.2021',
    section: '第五百六十六条',
    paragraph_id: 'article-566',
    start_offset: 0,
    end_offset: 1,
    quote:
      '合同解除后……已经履行的，根据履行情况和合同性质，当事人可以请求恢复原状或者采取其他补救措施，并有权请求赔偿损失。',
  },
  'span.case.zeng.reasoning': {
    span_id: 'span.case.zeng.reasoning',
    source_id: 'local-legal-rag.case.zeng',
    section: '裁判理由',
    paragraph_id: 'reasoning-1',
    start_offset: 0,
    end_offset: 1,
    quote:
      '被告经营场所已经关闭，无法继续提供健身服务，致使合同目的不能实现，原告有权解除合同并要求返还剩余预付款。',
  },
  'span.case.wang.reasoning': {
    span_id: 'span.case.wang.reasoning',
    source_id: 'local-legal-rag.case.wang',
    section: '裁判理由',
    paragraph_id: 'reasoning-1',
    start_offset: 0,
    end_offset: 1,
    quote:
      '健身房仍正常营业并可继续提供约定服务，原告因个人原因不再消费，不构成合同目的不能实现。',
  },
};

// ---- 规则 RuleRecord ----
export const RULE: RuleRecord = {
  contract_version: '1.3',
  rule_id: 'rule.service_contract.termination_refund.v1',
  title: '服务合同无法继续履行时的解除与返还规则（演示）',
  claim_types: ['服务合同解除与返还'],
  provisions: [
    {
      source_id: 'law.prc.civil_code.2021',
      provision_id: 'law.prc.civil_code.2021.article_563',
      article_no: '563',
      title: '中华人民共和国民法典第五百六十三条',
      valid_from: '2021-01-01',
      valid_to: null,
    },
    {
      source_id: 'law.prc.civil_code.2021',
      provision_id: 'law.prc.civil_code.2021.article_566',
      article_no: '566',
      title: '中华人民共和国民法典第五百六十六条',
      valid_from: '2021-01-01',
      valid_to: null,
    },
  ],
  conditions: [
    {
      condition_id: 'cond.contract_exists',
      label: '服务合同关系成立',
      predicate: '用户与健身房之间是否成立有效的服务合同关系',
      user_answerable: false,
      evidence_types: ['会员服务协议'],
      source_span_ids: ['span.civil-code.563'],
    },
    {
      condition_id: 'cond.payment_made',
      label: '消费者已支付费用',
      predicate: '消费者是否已向经营者支付预付款',
      user_answerable: false,
      evidence_types: ['付款凭证', '转账记录'],
      source_span_ids: ['span.civil-code.566'],
    },
    {
      condition_id: 'cond.obligation_unperformed',
      label: '存在未履行余额',
      predicate: '是否存在尚未消费、应予处理的剩余服务费用',
      user_answerable: false,
      evidence_types: ['消费记录', '会员系统截图'],
      source_span_ids: ['span.civil-code.566'],
    },
    {
      condition_id: 'cond.performance_impossible',
      label: '经营者无法继续履行',
      predicate: '经营者是否已经无法继续提供约定服务',
      user_answerable: true,
      evidence_types: ['停业通知', '企业登记状态', '聊天记录'],
      source_span_ids: ['span.civil-code.563'],
    },
    {
      condition_id: 'cond.alternative_performance',
      label: '存在替代履行安排',
      predicate: '经营者是否安排其他门店继续履行',
      user_answerable: true,
      evidence_types: ['转店通知', '门店公告'],
      source_span_ids: ['span.civil-code.563'],
    },
  ],
  condition_groups: [
    {
      group_id: 'group.termination_core',
      label: '解除与返还核心条件',
      role: 'CORE',
      operator: 'ALL',
      member_condition_ids: [
        'cond.contract_exists',
        'cond.payment_made',
        'cond.obligation_unperformed',
        'cond.performance_impossible',
      ],
    },
    {
      group_id: 'group.termination_alt',
      label: '替代履行诊断条件',
      role: 'DIAGNOSTIC',
      operator: 'ALL',
      member_condition_ids: ['cond.alternative_performance'],
    },
  ],
  exceptions: [],
  consequences: [
    {
      consequence_id: 'consequence.terminate_and_refund',
      consequence_type: '解除与返还',
      description: '候选后果：解除合同并处理未履行部分。',
      source_span_ids: ['span.civil-code.566'],
    },
  ],
  maturity: 'L3',
  source_spans: [SPANS['span.civil-code.563'], SPANS['span.civil-code.566']],
};

// ---- 案例 CaseRecord ----
export const SUPPORT_CASE: CaseRecord = {
  contract_version: '1.1',
  case_id: 'case.zeng-v-wuhan-fitness.2021',
  title: '曾某诉武汉某健身管理有限公司服务合同纠纷案',
  case_no: '（2021）鄂0104民初109号',
  court: '湖北省武汉市硚口区人民法院',
  judgment_date: '2021-12-15',
  cause: '服务合同纠纷',
  maturity: 'L2',
  claims: [
    {
      claim_id: 'claim.termination_refund',
      claim_type: '服务合同解除与返还',
      claimant: '曾某',
      respondent: '武汉某健身管理有限公司',
      requested_remedy: '解除合同并处理剩余服务费用',
      amount: null,
      invoked_rule_ids: ['rule.service_contract.termination_refund.v1'],
    },
  ],
  findings: [
    {
      finding_id: 'finding.operation_closed',
      predicate: '经营者经营场所关闭，无法继续提供健身服务',
      polarity: true,
      source_span_ids: ['span.case.zeng.reasoning'],
    },
  ],
  condition_findings: [
    { condition_id: 'cond.contract_exists', status: 'SATISFIED', finding_ids: [], confidence: 0.95, source_span_ids: ['span.case.zeng.reasoning'], human_verified: true },
    { condition_id: 'cond.payment_made', status: 'SATISFIED', finding_ids: [], confidence: 0.95, source_span_ids: ['span.case.zeng.reasoning'], human_verified: true },
    { condition_id: 'cond.obligation_unperformed', status: 'SATISFIED', finding_ids: [], confidence: 0.9, source_span_ids: ['span.case.zeng.reasoning'], human_verified: true },
    { condition_id: 'cond.performance_impossible', status: 'SATISFIED', finding_ids: ['finding.operation_closed'], confidence: 0.9, source_span_ids: ['span.case.zeng.reasoning'], human_verified: true },
    { condition_id: 'cond.alternative_performance', status: 'NOT_SATISFIED', finding_ids: [], confidence: 0.85, source_span_ids: ['span.case.zeng.reasoning'], human_verified: true },
  ],
  reasoning_steps: [
    {
      reasoning_id: 'reasoning.closure',
      premise_finding_ids: ['finding.operation_closed'],
      applied_rule_ids: ['rule.service_contract.termination_refund.v1'],
      conclusion: '经营场所关闭致使合同目的不能实现，支持解除并返还剩余费用。',
      source_span_ids: ['span.case.zeng.reasoning'],
    },
  ],
  decisions: [
    {
      decision_id: 'decision.claim.termination_refund',
      claim_id: 'claim.termination_refund',
      status: 'GRANTED',
      description: '支持解除合同并返还剩余预付款。',
      amount: null,
      source_span_ids: ['span.case.zeng.reasoning'],
    },
  ],
  source_spans: [SPANS['span.case.zeng.reasoning']],
};

export const LIMITING_CASE: CaseRecord = {
  contract_version: '1.1',
  case_id: 'case.wang-v-hz-fitness.2022',
  title: '王某诉杭州某健身服务有限公司服务合同纠纷案',
  case_no: '（2022）浙0106民初2034号',
  court: '杭州市西湖区人民法院',
  judgment_date: '2022-09-02',
  cause: '服务合同纠纷',
  maturity: 'L2',
  claims: [
    {
      claim_id: 'claim.termination_refund',
      claim_type: '服务合同解除与返还',
      claimant: '王某',
      respondent: '杭州某健身服务有限公司',
      requested_remedy: '解除合同并返还剩余预付款',
      amount: null,
      invoked_rule_ids: ['rule.service_contract.termination_refund.v1'],
    },
  ],
  findings: [
    {
      finding_id: 'finding.still_operating',
      predicate: '健身房仍正常营业并可继续提供约定服务',
      polarity: true,
      source_span_ids: ['span.case.wang.reasoning'],
    },
  ],
  condition_findings: [
    { condition_id: 'cond.contract_exists', status: 'SATISFIED', finding_ids: [], confidence: 0.95, source_span_ids: ['span.case.wang.reasoning'], human_verified: true },
    { condition_id: 'cond.payment_made', status: 'SATISFIED', finding_ids: [], confidence: 0.95, source_span_ids: ['span.case.wang.reasoning'], human_verified: true },
    { condition_id: 'cond.obligation_unperformed', status: 'SATISFIED', finding_ids: [], confidence: 0.9, source_span_ids: ['span.case.wang.reasoning'], human_verified: true },
    { condition_id: 'cond.performance_impossible', status: 'NOT_SATISFIED', finding_ids: ['finding.still_operating'], confidence: 0.9, source_span_ids: ['span.case.wang.reasoning'], human_verified: true },
    { condition_id: 'cond.alternative_performance', status: 'SATISFIED', finding_ids: [], confidence: 0.85, source_span_ids: ['span.case.wang.reasoning'], human_verified: true },
  ],
  reasoning_steps: [
    {
      reasoning_id: 'reasoning.personal_reason',
      premise_finding_ids: ['finding.still_operating'],
      applied_rule_ids: ['rule.service_contract.termination_refund.v1'],
      conclusion: '健身房仍可继续履行，原告因个人原因不再消费，不构成合同目的不能实现。',
      source_span_ids: ['span.case.wang.reasoning'],
    },
  ],
  decisions: [
    {
      decision_id: 'decision.claim.termination_refund',
      claim_id: 'claim.termination_refund',
      status: 'REJECTED',
      description: '驳回解除合同及退还预付款的诉讼请求。',
      amount: null,
      source_span_ids: ['span.case.wang.reasoning'],
    },
  ],
  source_spans: [SPANS['span.case.wang.reasoning']],
};

export const RULES: Record<string, RuleRecord> = { [RULE.rule_id]: RULE };
export const CASES: Record<string, CaseRecord> = {
  [SUPPORT_CASE.case_id]: SUPPORT_CASE,
  [LIMITING_CASE.case_id]: LIMITING_CASE,
};

export const PRESET_QUERY =
  '我在健身房充了5000元，店关门了，还有3000元没消费，老板不退款。';

export const DISCLAIMER = '该解释基于当前提供的信息，不替代律师针对完整材料出具的法律意见。';
