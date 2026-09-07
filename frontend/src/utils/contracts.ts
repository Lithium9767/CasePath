import type {
  CaseRecord,
  ResolvedSnapshot,
  RuleCondition,
  RuleRecord,
  SourceSpan,
} from '@/types';

export function conditionLabels(s: ResolvedSnapshot): Record<string, string> {
  const labels: Record<string, string> = {};
  for (const rule of Object.values(s.rules)) {
    for (const c of rule.conditions) labels[c.condition_id] = c.label;
  }
  for (const item of s.query_state.condition_states) {
    if (!labels[item.condition_id]) labels[item.condition_id] = item.condition_id;
  }
  return labels;
}

export function conditionMeta(s: ResolvedSnapshot): Record<string, RuleCondition> {
  const meta: Record<string, RuleCondition> = {};
  for (const rule of Object.values(s.rules)) {
    for (const c of rule.conditions) meta[c.condition_id] = c;
  }
  return meta;
}

export function rulesList(s: ResolvedSnapshot): RuleRecord[] {
  return Object.values(s.rules);
}

function resolveCases(s: ResolvedSnapshot, refs: { object_id: string }[]): CaseRecord[] {
  return refs
    .map((r) => s.cases[r.object_id])
    .filter((c): c is CaseRecord => Boolean(c));
}

export function supportCases(s: ResolvedSnapshot): CaseRecord[] {
  return resolveCases(s, s.retrieval_bundle.support_case_refs);
}

export function limitingCases(s: ResolvedSnapshot): CaseRecord[] {
  return resolveCases(s, s.retrieval_bundle.limiting_case_refs);
}

export function resolveSpans(s: ResolvedSnapshot, spanIds: string[]): SourceSpan[] {
  return spanIds
    .map((id) => s.spans[id])
    .filter((sp): sp is SourceSpan => Boolean(sp));
}
