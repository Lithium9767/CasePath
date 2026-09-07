import { Card, Tag } from 'antd';
import { CheckCircleOutlined, QuestionCircleOutlined } from '@ant-design/icons';
import type { ResolvedSnapshot } from '@/types';
import { StatusTag } from '@/components/StatusTag';
import { conditionLabels, rulesList } from '@/utils/contracts';
import { SESSION_LABEL, SESSION_COLOR } from '@/utils/status';

export function UnderstandingPanel({ snapshot }: { snapshot: ResolvedSnapshot }) {
  const qs = snapshot.query_state;
  const labels = conditionLabels(snapshot);
  const known = qs.condition_states.filter((s) => s.status !== 'UNKNOWN').length;
  const total = qs.condition_states.length;

  return (
    <Card
      size="small"
      title="系统当前理解"
      extra={<Tag color={SESSION_COLOR[qs.status]}>{SESSION_LABEL[qs.status]}</Tag>}
      className="panel-card"
    >
      {qs.candidate_claims.length > 0 && (
        <div className="panel-section">
          <div className="panel-section__title">候选请求权</div>
          <div className="bubble__claims">
            {qs.candidate_claims.map((c, i) => (
              <Tag key={`${c.claim_type}-${i}`} color="processing">
                {c.claim_type}
                <span style={{ marginLeft: 6, opacity: 0.8 }}>
                  {Math.round(c.confidence * 100)}%
                </span>
              </Tag>
            ))}
          </div>
          {qs.candidate_claims.map((c, i) => (
            <div key={i} className="panel-section__meta" style={{ marginTop: 4 }}>
              {c.requested_remedy}
            </div>
          ))}
        </div>
      )}
      <div className="panel-section">
        <div className="panel-section__title">
          规则条件状态
          <span className="panel-section__meta">
            {known}/{total} 已明确
          </span>
        </div>
        <div className="state-list">
          {qs.condition_states.map((s) => (
            <div key={s.condition_id} className="state-row">
              <span className="state-row__name">{labels[s.condition_id] ?? s.condition_id}</span>
              {s.status === 'UNKNOWN' ? (
                <QuestionCircleOutlined style={{ color: '#faad14', marginRight: 4 }} />
              ) : (
                <CheckCircleOutlined style={{ color: '#52c41a', marginRight: 4 }} />
              )}
              <StatusTag status={s.status} />
            </div>
          ))}
        </div>
      </div>
      {(rulesList(snapshot).length > 0
        ? rulesList(snapshot).map((r) => (
            <div key={r.rule_id} className="panel-section__meta">
              规则 {r.title} · 成熟度 {r.maturity}
            </div>
          ))
        : snapshot.retrieval_bundle.rule_refs.map((r) => (
            <div key={r.object_id} className="panel-section__meta">
              候选规则 {r.object_id}
            </div>
          )))}
      {snapshot.retrieval_bundle.degraded && (
        <Tag color="warning" style={{ marginTop: 8 }}>
          案例证据不足：{snapshot.retrieval_bundle.degradation_reason ?? '可比对案例不够'}
        </Tag>
      )}
    </Card>
  );
}
