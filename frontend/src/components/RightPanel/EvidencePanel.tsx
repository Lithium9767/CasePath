import { Card, Collapse, List, Tag, Typography } from 'antd';
import { FileSearchOutlined, PaperClipOutlined } from '@ant-design/icons';
import type { ResolvedSnapshot } from '@/types';
import { conditionLabels, resolveSpans } from '@/utils/contracts';

const { Text, Paragraph } = Typography;

export function EvidencePanel({ snapshot }: { snapshot: ResolvedSnapshot }) {
  const plan = snapshot.explanation_plan;
  const labels = conditionLabels(snapshot);

  return (
    <Card size="small" title="证据提示与原文引用" className="panel-card">
      <div className="panel-section">
        <div className="panel-section__title">
          <PaperClipOutlined /> 建议保存的证据
        </div>
        {plan.evidence_actions.length > 0 ? (
          <List
            size="small"
            dataSource={plan.evidence_actions}
            renderItem={(e) => (
              <List.Item>
                <List.Item.Meta
                  title={e.description}
                  description={
                    e.related_condition_ids.length > 0
                      ? e.related_condition_ids.map((cid) => labels[cid] ?? cid).join('、')
                      : undefined
                  }
                />
              </List.Item>
            )}
          />
        ) : (
          <Text type="secondary">暂无</Text>
        )}
      </div>

      <div className="panel-section">
        <div className="panel-section__title">
          <FileSearchOutlined /> 原文引用
        </div>
        {plan.citations.length > 0 ? (
          <Collapse
            size="small"
            ghost
            items={plan.citations.map((c, i) => ({
              key: String(i),
              label: (
                <span className="citation-label">
                  {c.verified ? '✅' : '⚠️'} {c.citation_id} · {c.supports}
                  {!c.verified && <Tag color="warning" style={{ marginLeft: 6 }}>未核验</Tag>}
                </span>
              ),
              children: (
                <div>
                  {resolveSpans(snapshot, c.source_span_ids).map((sp) => (
                    <div key={sp.span_id}>
                      <Paragraph className="citation-text">“{sp.quote}”</Paragraph>
                      <Tag color="default" style={{ fontSize: 11 }}>
                        {sp.source_id} · {sp.section ?? ''}
                      </Tag>
                    </div>
                  ))}
                </div>
              ),
            }))}
          />
        ) : (
          <Text type="secondary">暂无引用</Text>
        )}
      </div>
    </Card>
  );
}
