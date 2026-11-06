import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Input, Segmented, Spin } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { ResolvedSnapshot } from '@/types';
import { retrievalGraph } from '@/utils/retrievalGraph';
import './graph-explorer.css';

interface Node { id: string; object_id: string; category: string; name: string }
interface Edge { source: string; target: string; relation: string; source_object_id: string; target_object_id: string }
const categories = ['LegalSource','Provision','LegalRule','ConditionGroup','RuleCondition','RuleException','LegalConsequence','Case','Claim','CourtFinding','ConditionFinding','ReasoningStep','DecisionItem','SourceSpan'];
const names = ['法律来源','法条','规则','条件组','条件','例外','法律后果','案例','请求','法院事实','条件认定','裁判理由','裁判结果','原文证据'];
const colors = ['#8b9eff','#708bff','#42bcff','#51dddc','#29c9b3','#edb763','#c3a1ff','#ffbd69','#f18f92','#d59de8','#69d6a5','#ea96c2','#fabd8c','#7c91aa'];

export function LiveRetrievalGraph({ snapshot }: { snapshot: ResolvedSnapshot | null }) {
  const [graph, setGraph] = useState<{ nodes: Node[]; edges: Edge[]; release: string } | null>(null);
  const [error, setError] = useState('');
  const [count, setCount] = useState(0);
  const [reload, setReload] = useState(0);
  const [mode, setMode] = useState('全图');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Node | null>(null);
  const [expanded, setExpanded] = useState(false);
  const paths = useMemo(() => snapshot ? retrievalGraph(snapshot) : { nodes: [], edges: [] }, [snapshot]);
  useEffect(() => {
    const controller = new AbortController();
    setGraph(null); setError(''); setCount(0);
    async function load() {
      const result = { nodes: [] as Node[], edges: [] as Edge[], release: '' };
      // 完成所有分页才展示，不能将半张图标记为全图；固定同一发布版本。
      for (const kind of ['nodes', 'edges'] as const) {
        let offset: number | null = 0;
        while (offset !== null) {
          const params = new URLSearchParams({ kind, offset: String(offset), limit: '2000' });
          if (result.release) params.set('release', result.release);
          const response = await fetch(`${import.meta.env.VITE_API_BASE ?? ''}/v1/graph?${params}`, { signal: controller.signal });
          if (!response.ok) throw new Error(`Neo4j 图谱读取失败（HTTP ${response.status}），请确认后端已更新且在线图可用。`);
          const page = await response.json();
          if (result.release && result.release !== page.release_id) throw new Error('数据版本变化，请重新加载');
          result.release = page.release_id;
          result[kind].push(...page.items);
          offset = page.next_offset;
          setCount(result.nodes.length + result.edges.length);
        }
      }
      setGraph(result);
    }
    load().catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [reload]);
  const view = useMemo(() => {
    if (!graph) return { data: [], links: [], matched: 0 };
    const active = new Set(paths.nodes.map(n => n.id));
    const keys = new Set(paths.edges.map(e => JSON.stringify([e.source, e.relation, e.target])));
    const nodes = graph.nodes.filter(n => mode === '全图' || active.has(n.object_id));
    const ids = new Set(nodes.map(n => n.id));
    const totals = new Map<string, number>();
    const seen = new Map<string, number>();
    nodes.forEach(n => totals.set(n.category, (totals.get(n.category) ?? 0) + 1));
    // 静态星簇布局为 O(N)，不对全图执行昂贵的力模拟。
    const data = nodes.map(n => {
      const i = seen.get(n.category) ?? 0; seen.set(n.category, i + 1);
      const group = Math.max(0, categories.indexOf(n.category));
      const radius = Math.sqrt((i + 1) / totals.get(n.category)!) * 110;
      const hot = active.has(n.object_id);
      const found = !!search.trim() && `${n.name} ${n.object_id}`.toLowerCase().includes(search.toLowerCase());
      return { ...n, x: (group % 4) * 290 + Math.cos(i * 2.399963) * radius,
        y: Math.floor(group / 4) * 270 + Math.sin(i * 2.399963) * radius,
        symbolSize: found ? 16 : hot ? 10 : 3,
        itemStyle: { color: found ? '#fff' : colors[group], opacity: hot || found || !active.size ? 0.95 : 0.28 },
        label: { show: found || (hot && mode !== '全图'), color: '#e7f4ff', width: 130, overflow: 'truncate', position: 'right' } };
    });
    let matched = 0;
    const links = graph.edges.filter(e => ids.has(e.source) && ids.has(e.target)).map(e => {
      // 精确匹配有向三元组，不因两个端点高亮就捏造路径边。
      const hot = keys.has(JSON.stringify([e.source_object_id, e.relation, e.target_object_id]));
      if (hot) matched++;
      return { ...e, lineStyle: { color: hot ? '#67e8f9' : '#42617c', opacity: hot ? 0.95 : 0.055, width: hot ? 2.4 : 0.5 },
        label: { show: hot && mode !== '全图', formatter: e.relation, color: '#97ebf6', fontSize: 10 } };
    }).sort((a, b) => a.lineStyle.width - b.lineStyle.width);
    return { data, links, matched };
  }, [graph, paths, mode, search]);
  return <Card className={`panel-card graph-explorer ${expanded ? 'graph-explorer--expanded' : ''}`}>
    <div className="graph-heading"><div><small>CASEPATH / KNOWLEDGE ATLAS</small><h3>沿着证据，看见关联</h3><p>规则与案例双层图谱 · 本轮真实检索路径</p></div>
      <Button onClick={() => setExpanded(!expanded)}>{expanded ? '收起' : '展开画布'}</Button></div>
    <div className="graph-toolbar"><Segmented options={['全图', '本轮路径']} value={mode} onChange={v => setMode(String(v))} />
      <Input allowClear placeholder="搜索节点名称 / ID" value={search} onChange={e => setSearch(e.target.value)} style={{ width: 210 }} />
      <Button onClick={() => setReload(v => v + 1)}>重新加载</Button></div>
    {error ? <Alert type="warning" showIcon message={error} /> : !graph ? <div className="graph-loading"><Spin /><p>读取 Neo4j 当前版本… 已载入 {count.toLocaleString()} 项</p></div> : <>
      <div className="graph-stats">{graph.nodes.length.toLocaleString()} <small>实体</small>　{graph.edges.length.toLocaleString()} <small>关系</small>　{view.matched} <small>路径高亮</small></div>
      <div className="graph-canvas"><ReactECharts key={`${mode}-${reload}`} style={{ height: expanded ? '65vh' : 490 }} notMerge
        option={{ animation: false, tooltip: { renderMode: 'richText', confine: true }, series: [{ type: 'graph', layout: 'none', roam: true,
          scaleLimit: { min: 0.15, max: 12 }, data: view.data, links: view.links, edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 4,
          emphasis: { focus: 'adjacency', label: { show: true, color: '#fff' } } }] }}
        onEvents={{ click: (e: { dataType: string; data: Node }) => { if (e.dataType === 'node') setSelected(e.data); } }} />
        <div className="graph-hint">滚轮缩放 · 拖动平移 · 点击查看节点</div></div>
      <div className="graph-legend">{names.map((name, i) => <span key={name}><i style={{ background: colors[i] }} />{name}</span>)}</div>
      {selected && <div className="graph-selection"><strong>{selected.name}</strong><code>{selected.category} · {selected.object_id}</code></div>}
      <div className="graph-footnote">当前版本完整读取 · {graph.release.slice(0, 12)} · 画布显示 {view.data.length} 个节点<br />青色关系仅表示检索路径，不表示法院支持或否定。
        {view.matched < paths.edges.length && <p>部分返回路径未匹配当前图，请检查数据版本；不会补画不存在的关系。</p>}
        {!paths.edges.length && <p>本轮未返回检索路径，全图仍可浏览。</p>}</div>
    </>}
  </Card>;
}
