import { Card, Col, Row, Tag, Typography } from 'antd';
import { CheckCircleOutlined, MinusCircleOutlined } from '@ant-design/icons';
import type { CaseRecord, ResolvedSnapshot } from '@/types';
import { StatusTag } from '@/components/StatusTag';
import { conditionLabels, resolveSpans } from '@/utils/contracts';
import { DECISION_COLOR, DECISION_LABEL } from '@/utils/status';

const { Text, Paragraph } = Typography;

const CaseCard = ({
  record,
  kind,
  labels,
  snapshot,
}: {
  record: CaseRecord;
  kind: 'support' | 'limiting';
  labels: Record<string, string>;
  snapshot: ResolvedSnapshot;
}) => {
  const isSupport = kind === 'support';
  const spanIds = Array.from(
    new Set(record.condition_findings.flatMap((f) => f.source_span_ids)),
  );
  return (
    <Card
      size="small"
      className={`case-card ${isSupport ? 'case-card--support' : 'case-card--limiting'}`}
      title={
        <span className="case-card__title">
          {isSupport ? (
            <CheckCircleOutlined style={{ color: '#52c41a', marginRight: 6 }} />
          ) : (
            <MinusCircleOutlined style={{ color: '#fa541c', marginRight: 6 }} />
          )}
          {isSupport ? '支持案例' : '限制案例'}
        </span>
      }
    >
      <div className="case-card__meta">
        <Text strong>{record.title}</Text>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {record.court ?? '未知法院'} · {record.judgment_date ?? '未知日期'} · 成熟度{' '}
          {record.maturity}
        </Text>
      </div>

      {record.claims.length > 0 && (
        <div className="panel-section__meta" style={{ marginBottom: 6 }}>
          请求：{record.claims[0].claim_type}（{record.claims[0].requested_remedy}）
        </div>
      )}

      <div className="case-card__findings">
        {record.condition_findings.map((f) => (
          <div key={f.condition_id} className="case-card__finding-row">
            <span className="case-card__finding-name">
              {labels[f.condition_id] ?? f.condition_id}
            </span>
            <StatusTag status={f.status} />
          </div>
        ))}
      </div>

      {record.decisions.length > 0 && (
        <div className="case-card__decision">
          <Text strong style={{ fontSize: 12 }}>
            裁判结果：
          </Text>
          <Tag color={DECISION_COLOR[record.decisions[0].status]}>
            {DECISION_LABEL[record.decisions[0].status]}
          </Tag>
          <Text style={{ fontSize: 12 }}>{record.decisions[0].description}</Text>
        </div>
      )}

      {spanIds.length > 0 && (
        <Paragraph className="case-card__finding-text">
          “{resolveSpans(snapshot, spanIds).map((s) => s.quote).join('')}”
        </Paragraph>
      )}
    </Card>
  );
};

function CaseRefCard({
  title,
  refs,
}: {
  title: string;
  refs: { object_id: string; reasons: string[]; score: number }[];
}) {
  if (refs.length === 0) {
    return (
      <Card size="small">
        <Text type="secondary">{title}：当前没有足够案例证据，不表示没有法律依据。</Text>
      </Card>
    );
  }
  return (
    <Card size="small" title={title}>
      {refs.map((item) => (
        <div key={item.object_id} className="case-card__meta" style={{ marginBottom: 8 }}>
          <Text strong>{item.object_id}</Text>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {item.reasons.join('；') || `score ${item.score.toFixed(3)}`}
            </Text>
          </div>
        </div>
      ))}
    </Card>
  );
}

export function CasePanel({ snapshot }: { snapshot: ResolvedSnapshot }) {
  const labels = conditionLabels(snapshot);
  const bundle = snapshot.retrieval_bundle;
  const support = bundle.support_case_refs
    .map((r) => snapshot.cases[r.object_id])
    .filter((c): c is CaseRecord => Boolean(c));
  const limiting = bundle.limiting_case_refs
    .map((r) => snapshot.cases[r.object_id])
    .filter((c): c is CaseRecord => Boolean(c));
  const hasResolved = support.length > 0 || limiting.length > 0;

  return (
    <Card size="small" title="案例证据" className="panel-card">
      {bundle.degraded && (
        <Text type="warning" style={{ display: 'block', marginBottom: 8 }}>
          {bundle.degradation_reason ?? '案例证据不足，已降级展示。'}
          这不等于没有相关法律依据。
        </Text>
      )}
      {hasResolved ? (
        <Row gutter={[12, 12]}>
          <Col span={12}>
            {support.length > 0 ? (
              <CaseCard record={support[0]} kind="support" labels={labels} snapshot={snapshot} />
            ) : (
              <CaseRefCard title="支持案例" refs={bundle.support_case_refs} />
            )}
          </Col>
          <Col span={12}>
            {limiting.length > 0 ? (
              <CaseCard record={limiting[0]} kind="limiting" labels={labels} snapshot={snapshot} />
            ) : (
              <CaseRefCard title="限制案例" refs={bundle.limiting_case_refs} />
            )}
          </Col>
        </Row>
      ) : (
        <Row gutter={[12, 12]}>
          <Col span={12}>
            <CaseRefCard title="支持案例" refs={bundle.support_case_refs} />
          </Col>
          <Col span={12}>
            <CaseRefCard title="限制 / 边界案例" refs={[...bundle.limiting_case_refs, ...bundle.boundary_case_refs]} />
          </Col>
        </Row>
      )}
    </Card>
  );
}
