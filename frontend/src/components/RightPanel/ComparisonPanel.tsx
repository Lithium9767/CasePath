import { Card, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { ConditionComparison, ResolvedSnapshot } from '@/types';
import { conditionLabels } from '@/utils/contracts';
import { formatScore } from '@/utils/status';

interface Row {
  key: string;
  condition: string;
  entropy: number;
  disagreement: number;
  infoGain: number;
  branchReduction: number;
  counts: string;
}

export function ComparisonPanel({ snapshot }: { snapshot: ResolvedSnapshot }) {
  const bundle = snapshot.comparison_bundle;
  if (!bundle) return null;
  if (bundle.condition_comparisons.length === 0) return null;

  const labels = conditionLabels(snapshot);
  const rankOrder = new Map(bundle.ranked_condition_ids.map((id, i) => [id, i]));
  const comparisons = [...bundle.condition_comparisons].sort(
    (a, b) => (rankOrder.get(a.condition_id) ?? 99) - (rankOrder.get(b.condition_id) ?? 99),
  );

  const rows: Row[] = comparisons.map((c: ConditionComparison) => ({
    key: c.condition_id,
    condition: labels[c.condition_id] ?? c.condition_id,
    entropy: c.condition_entropy,
    disagreement: c.case_disagreement_score,
    infoGain: c.expected_information_gain,
    branchReduction: c.expected_branch_reduction,
    counts: Object.entries(c.status_counts)
      .map(([k, v]) => `${k}:${v}`)
      .join(' '),
  }));

  const columns: ColumnsType<Row> = [
    { title: '规则条件', dataIndex: 'condition', key: 'condition' },
    { title: '状态计数', dataIndex: 'counts', key: 'counts', render: (v: string) => <span style={{ fontSize: 12 }}>{v}</span> },
    {
      title: '案例分化',
      dataIndex: 'disagreement',
      key: 'disagreement',
      align: 'center',
      render: (v: number) => <Tag color={v >= 0.6 ? 'red' : 'orange'}>{formatScore(v)}</Tag>,
    },
    { title: '信息增益', dataIndex: 'infoGain', key: 'infoGain', align: 'center', render: (v: number) => formatScore(v) },
    { title: '分支缩减', dataIndex: 'branchReduction', key: 'branchReduction', align: 'center', render: (v: number) => formatScore(v) },
    { title: '熵', dataIndex: 'entropy', key: 'entropy', align: 'center', render: (v: number) => formatScore(v) },
  ];

  return (
    <Card
      size="small"
      title="条件分化指标（P4 ComparisonBundle）"
      extra={
        <Tooltip title="同一规则条件下正反案例的裁判分化程度，用于追问价值与解释空间缩减">
          <span className="panel-card__hint">案例分化</span>
        </Tooltip>
      }
      className="panel-card"
    >
      <Table<Row> columns={columns} dataSource={rows} pagination={false} size="small" bordered />
      {bundle.degraded && (
        <Tag color="warning" style={{ marginTop: 8 }}>
          分化检测已降级：{bundle.degradation_reason ?? '缺少真实对照案例'}
        </Tag>
      )}
    </Card>
  );
}
