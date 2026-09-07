import { Card, Table, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { ConditionStatus, ResolvedSnapshot } from '@/types';
import { StatusTag } from '@/components/StatusTag';
import { conditionMeta, limitingCases, rulesList, supportCases } from '@/utils/contracts';

interface Row {
  key: string;
  condition: string;
  user: ConditionStatus;
  support: ConditionStatus | null;
  limiting: ConditionStatus | null;
}

const statusOf = (
  findings: { condition_id: string; status: ConditionStatus }[],
  conditionId: string,
): ConditionStatus | null => findings.find((f) => f.condition_id === conditionId)?.status ?? null;

export function ProjectionMatrix({ snapshot }: { snapshot: ResolvedSnapshot }) {
  const meta = conditionMeta(snapshot);
  const rules = rulesList(snapshot);
  const conditionIds =
    snapshot.query_state.condition_states.map((item) => item.condition_id).length > 0
      ? snapshot.query_state.condition_states.map((item) => item.condition_id)
      : (rules[0]?.conditions.map((c) => c.condition_id) ?? Object.keys(meta));

  const userStates = new Map(
    snapshot.query_state.condition_states.map((s) => [s.condition_id, s.status]),
  );
  const supportFindings = supportCases(snapshot).flatMap((c) => c.condition_findings);
  const limitingFindings = limitingCases(snapshot).flatMap((c) => c.condition_findings);

  const rows: Row[] = conditionIds.map((cid) => ({
    key: cid,
    condition: meta[cid]?.label ?? cid,
    user: userStates.get(cid) ?? 'UNKNOWN',
    support: statusOf(supportFindings, cid),
    limiting: statusOf(limitingFindings, cid),
  }));

  const columns: ColumnsType<Row> = [
    {
      title: '规则条件',
      dataIndex: 'condition',
      key: 'condition',
      render: (v: string) => <span className="matrix-condition">{v}</span>,
    },
    {
      title: '用户案',
      dataIndex: 'user',
      key: 'user',
      align: 'center',
      render: (v: ConditionStatus) => <StatusTag status={v} />,
    },
    {
      title: '支持案例',
      dataIndex: 'support',
      key: 'support',
      align: 'center',
      render: (v: ConditionStatus | null) =>
        v ? <StatusTag status={v} /> : <span style={{ color: '#bbb' }}>—</span>,
    },
    {
      title: '限制案例',
      dataIndex: 'limiting',
      key: 'limiting',
      align: 'center',
      render: (v: ConditionStatus | null) =>
        v ? <StatusTag status={v} /> : <span style={{ color: '#bbb' }}>—</span>,
    },
  ];

  return (
    <Card
      size="small"
      title="规则条件投影矩阵"
      extra={
        <Tooltip title="将用户事实与正反案例统一投影到同一规则条件空间（CasePath 跨层投影核心）">
          <span className="panel-card__hint">跨层投影</span>
        </Tooltip>
      }
      className="panel-card"
    >
      <Table<Row> columns={columns} dataSource={rows} pagination={false} size="small" bordered />
    </Card>
  );
}
