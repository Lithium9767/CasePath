import { Card, Empty } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { ResolvedSnapshot } from '@/types';
import { retrievalGraph } from '@/utils/retrievalGraph';

export function LiveRetrievalGraph({ snapshot }: { snapshot: ResolvedSnapshot }) {
  const { nodes, edges } = retrievalGraph(snapshot);
  return <Card size="small" title="本轮检索路径" className="panel-card"
    extra={<span className="panel-card__hint">后端返回的局部路径，非完整知识图谱</span>}>
    {edges.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
      description="本轮未返回可展示的图路径；不代表没有相关规则或案例。" /> :
      <ReactECharts style={{ height: 460, width: '100%' }} option={{
        tooltip: { renderMode: 'richText' },
        series: [{ type: 'graph', layout: 'force', roam: true, draggable: true,
          force: { repulsion: 320, edgeLength: 140 },
          edgeSymbol: ['none', 'arrow'],
          data: nodes.map((n) => ({ ...n, symbolSize: 30,
            itemStyle: { color: n.id === snapshot.next_question?.condition_id ? '#ef4444' : '#3568b0' } })),
          links: edges.map((e) => ({ ...e, label: { show: true, formatter: e.relation } })),
          label: { show: true, position: 'bottom', width: 150, overflow: 'truncate' },
        }],
      }} />}
    <div className="panel-card__hint">箭头与关系名来自 graph_paths；未返回详情的节点显示 ID。颜色不表示法院支持或否定。</div>
  </Card>;
}
