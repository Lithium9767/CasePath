// 前端 mock 知识图谱：严格从 scenario.ts 的 RuleRecord / CaseRecord 推导「两层知识图谱」。
// 对齐 CasePath-完整优化执行方案-v2.md 第 3 节：规范规则层 + 案例适用层 + 桥接关系。

import type { ConditionStatus, ResolvedSnapshot } from '@/types';
import { RULE, SUPPORT_CASE, LIMITING_CASE } from './scenario';

export type GraphNodeType =
  | 'source' // LegalSource 法律来源
  | 'provision' // ProvisionVersion 法条
  | 'rule' // LegalRule 规则
  | 'condition' // RuleCondition 规则条件
  | 'consequence' // LegalConsequence 法律后果
  | 'support_case' // 支持案例
  | 'limiting_case' // 限制案例
  | 'claim' // Claim 请求权
  | 'decision'; // DecisionItem 判决

export interface GraphNode {
  id: string;
  name: string;
  type: GraphNodeType;
  conditionId?: string;
  x: number;
  y: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  relation: string;
  status?: ConditionStatus; // 桥接边 INSTANTIATES 携带五值状态
}

export const CATEGORY_META: Record<GraphNodeType, { label: string; color: string }> = {
  source: { label: '法律来源', color: '#94a3b8' },
  provision: { label: '法条', color: '#7c3aed' },
  rule: { label: '规则', color: '#1e5eff' },
  condition: { label: '规则条件', color: '#16a34a' },
  consequence: { label: '法律后果', color: '#d97706' },
  support_case: { label: '支持案例', color: '#0891b2' },
  limiting_case: { label: '限制案例', color: '#ea580c' },
  claim: { label: '请求权', color: '#db2777' },
  decision: { label: '判决', color: '#059669' },
};

export const GRAPH_NODE_TYPES = Object.keys(CATEGORY_META) as GraphNodeType[];

export const STATUS_COLOR: Record<ConditionStatus, string> = {
  SATISFIED: '#16a34a',
  NOT_SATISFIED: '#dc2626',
  UNKNOWN: '#d97706',
  CONFLICTING: '#7c3aed',
  NOT_APPLICABLE: '#9ca3af',
};

export const STATUS_LABEL: Record<ConditionStatus, string> = {
  SATISFIED: '满足',
  NOT_SATISFIED: '不满足',
  UNKNOWN: '未知',
  CONFLICTING: '冲突',
  NOT_APPLICABLE: '不适用',
};

const DECISION_LABEL: Record<string, string> = {
  GRANTED: '支持解除返还',
  REJECTED: '驳回',
  PARTIALLY_GRANTED: '部分支持',
  WITHDRAWN: '撤回',
  UNKNOWN: '未知',
};

const SOURCE_ID = 'law.prc.civil_code.2021';

export function buildGraph(): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];

  // ---- 第一层：规范规则层 ----
  nodes.push({ id: SOURCE_ID, name: '《民法典》', type: 'source', x: 0, y: -230 });

  RULE.provisions.forEach((p, i) => {
    nodes.push({
      id: p.provision_id,
      name: `第 ${p.article_no} 条`,
      type: 'provision',
      x: (i - (RULE.provisions.length - 1) / 2) * 140,
      y: -150,
    });
    edges.push({ source: SOURCE_ID, target: p.provision_id, relation: 'CONTAINS' });
    edges.push({ source: p.provision_id, target: RULE.rule_id, relation: 'EXPRESSES' });
  });

  nodes.push({ id: RULE.rule_id, name: '解除与返还规则', type: 'rule', x: 0, y: -60 });

  RULE.conditions.forEach((c, i) => {
    nodes.push({
      id: c.condition_id,
      name: c.label,
      type: 'condition',
      conditionId: c.condition_id,
      x: -320 + (i % 3) * 160,
      y: 40 + Math.floor(i / 3) * 90,
    });
    edges.push({ source: RULE.rule_id, target: c.condition_id, relation: 'REQUIRES' });
  });

  RULE.consequences.forEach((c) => {
    nodes.push({ id: c.consequence_id, name: c.consequence_type, type: 'consequence', x: 0, y: 210 });
    edges.push({ source: RULE.rule_id, target: c.consequence_id, relation: 'LEADS_TO' });
  });

  // ---- 第二层：案例适用层 + 桥接 ----
  const caseEntries: { rec: typeof SUPPORT_CASE; type: 'support_case' | 'limiting_case' }[] = [
    { rec: SUPPORT_CASE, type: 'support_case' },
    { rec: LIMITING_CASE, type: 'limiting_case' },
  ];
  caseEntries.forEach(({ rec, type }, idx) => {
    const caseX = 320;
    const caseY = idx === 0 ? -100 : 100;
    nodes.push({
      id: rec.case_id,
      name: type === 'support_case' ? '曾某诉健身案' : '王某诉健身案',
      type,
      x: caseX,
      y: caseY,
    });

    rec.claims.forEach((claim) => {
      const claimId = `${claim.claim_id}@${rec.case_id}`;
      nodes.push({ id: claimId, name: claim.claim_type, type: 'claim', x: 480, y: caseY });
      edges.push({ source: rec.case_id, target: claimId, relation: 'HAS_CLAIM' });
      claim.invoked_rule_ids.forEach((rid) =>
        edges.push({ source: claimId, target: rid, relation: 'INVOKES' }),
      );
    });

    // 桥接边：案例 -> 规则条件（INSTANTIATES，带五值状态）
    rec.condition_findings.forEach((f) =>
      edges.push({ source: rec.case_id, target: f.condition_id, relation: 'INSTANTIATES', status: f.status }),
    );

    rec.decisions.forEach((d) => {
      const decisionId = `${d.decision_id}@${rec.case_id}`;
      nodes.push({
        id: decisionId,
        name: DECISION_LABEL[d.status] ?? d.status,
        type: 'decision',
        x: 480,
        y: caseY + (idx === 0 ? -70 : 70),
      });
      edges.push({ source: `${d.claim_id}@${rec.case_id}`, target: decisionId, relation: 'RESOLVED_BY' });
    });
  });

  return { nodes, edges };
}

// 当前快照下「激活」的节点集合（随对话联动）。
export function activeNodeIds(snapshot: ResolvedSnapshot): Set<string> {
  const ids = new Set<string>();
  const rb = snapshot.retrieval_bundle;
  rb.rule_refs.forEach((r) => ids.add(r.object_id));
  rb.support_case_refs.forEach((r) => ids.add(r.object_id));
  rb.limiting_case_refs.forEach((r) => ids.add(r.object_id));
  rb.boundary_case_refs.forEach((r) => ids.add(r.object_id));

  if (snapshot.next_question) {
    // 追问阶段：聚焦当前条件
    ids.add(snapshot.next_question.condition_id);
  } else {
    // 解释阶段：高亮完整已解析路径（规则 + 全部条件 + 后果 + 法条 + 来源）
    for (const rule of Object.values(snapshot.rules)) {
      rule.conditions.forEach((c) => ids.add(c.condition_id));
      rule.consequences.forEach((c) => ids.add(c.consequence_id));
      rule.provisions.forEach((p) => {
        ids.add(p.provision_id);
        ids.add(p.source_id);
      });
    }
  }
  return ids;
}

// 当前追问的条件（用于特殊强调）。
export function focusConditionId(snapshot: ResolvedSnapshot): string | null {
  return snapshot.next_question?.condition_id ?? null;
}
