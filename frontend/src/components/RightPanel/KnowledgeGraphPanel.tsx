import { Card, Tag } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import type { ConditionStatus, ResolvedSnapshot } from '@/types';
import {
  activeNodeIds,
  buildGraph,
  CATEGORY_META,
  focusConditionId,
  GRAPH_NODE_TYPES,
  STATUS_COLOR,
  STATUS_LABEL,
  type GraphNode,
} from '@/mock/graph';
import { conditionLabels } from '@/utils/contracts';
import { LiveRetrievalGraph } from './LiveRetrievalGraph';

interface Props {
  snapshot: ResolvedSnapshot;
}

export function KnowledgeGraphPanel({ snapshot }: Props) {
  // 与 API 模式保持一致。真实接口绝不能混入固定健身房演示图。
  if (import.meta.env.VITE_USE_MOCK !== 'true') return <LiveRetrievalGraph snapshot={snapshot} />;
  const { nodes, edges } = buildGraph();
  const active = activeNodeIds(snapshot);
  const focusId = focusConditionId(snapshot);
  const labels = conditionLabels(snapshot);

  const statusMap = new Map<string, ConditionStatus>(
    snapshot.query_state.condition_states.map((s) => [s.condition_id, s.status]),
  );
  const nameMap = new Map<string, string>(nodes.map((n: GraphNode) => [n.id, n.name]));

  const option: EChartsOption = {
    animationDuration: 400,
    tooltip: {
      formatter: (params: unknown) => {
        const p = params as { dataType?: string; name?: string; data?: { tooltipText?: string } };
        return p.data?.tooltipText ?? p.name ?? '';
      },
    },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        scaleLimit: { min: 0.4, max: 3 },
        force: {
          repulsion: 360,
          edgeLength: [55, 130],
          gravity: 0.06,
          friction: 0.4,
        },
        emphasis: { focus: 'adjacency', scale: true },
        data: nodes.map((n) => {
          const isCondition = n.type === 'condition';
          const status = isCondition ? (statusMap.get(n.conditionId ?? '') ?? 'UNKNOWN') : undefined;
          const isActive = active.has(n.id);
          const isFocus = n.id === focusId;
          const color = isCondition ? STATUS_COLOR[status as ConditionStatus] : CATEGORY_META[n.type].color;
          return {
            id: n.id,
            name: n.name,
            x: n.x,
            y: n.y,
            symbolSize: isFocus ? 62 : isActive ? 42 : 24,
            itemStyle: {
              color: isActive ? color : '#d6dbe3',
              borderColor: isFocus ? '#ff4d4f' : isActive ? 'rgba(0,0,0,0.15)' : 'rgba(0,0,0,0.05)',
              borderWidth: isFocus ? 3 : 1,
              opacity: isActive ? 1 : 0.35,
              shadowBlur: isFocus ? 18 : 0,
              shadowColor: isFocus ? 'rgba(255,77,79,0.5)' : 'transparent',
            },
            label: {
              show: true,
              position: 'bottom',
              distance: 4,
              fontSize: 10,
              color: isActive ? '#1f2733' : '#a8b0bd',
              fontWeight: isFocus ? 'bold' : 'normal',
            },
            tooltipText: isCondition
              ? `${n.name}（当前：${STATUS_LABEL[status as ConditionStatus]}）`
              : n.name,
          };
        }),
        links: edges.map((e) => {
          const isBridge = e.relation === 'INSTANTIATES';
          const on = active.has(e.source) && active.has(e.target);
          const isFocusBridge = isBridge && e.target === focusId;
          const color = isBridge
            ? (e.status ? STATUS_COLOR[e.status] : '#cbd5e1')
            : '#a3b8ff';
          return {
            source: e.source,
            target: e.target,
            lineStyle: {
              color: on ? color : '#e9edf2',
              width: isFocusBridge ? 3.5 : on ? 2 : 1,
              opacity: on ? 0.9 : 0.3,
              curveness: 0.12,
            },
            tooltipText: `${nameMap.get(e.source) ?? e.source} —${e.relation}${
              e.status ? `(${STATUS_LABEL[e.status]})` : ''
            }→ ${nameMap.get(e.target) ?? e.target}`,
          };
        }),
      },
    ],
  };

  return (
    <Card
      size="small"
      title="两层知识图谱（Mock 演示）"
      extra={<span className="panel-card__hint">随对话自动高亮</span>}
      className="panel-card"
    >
      <ReactECharts option={option} style={{ height: 460, width: '100%' }} />
      <div className="graph-legend">
        {GRAPH_NODE_TYPES.map((t) => (
          <Tag key={t} color={CATEGORY_META[t].color} style={{ color: '#1f2733' }}>
            {CATEGORY_META[t].label}
          </Tag>
        ))}
      </div>
      <div className="graph-focus">
        <span>桥接边（案例→条件）：</span>
        <span style={{ color: STATUS_COLOR.SATISFIED }}>绿=满足</span> ·{' '}
        <span style={{ color: STATUS_COLOR.NOT_SATISFIED }}>红=不满足</span>
        {focusId && (
          <span style={{ marginLeft: 12 }}>
            当前追问：<strong style={{ color: '#ff4d4f' }}>{labels[focusId] ?? focusId}</strong>
          </span>
        )}
      </div>
    </Card>
  );
}
