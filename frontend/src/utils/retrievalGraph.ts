import type { ResolvedSnapshot } from '../types';

// Live 只画后端本轮实际返回的有向路径；不根据 ID 名称猜关系，不补演示案例。
// API 未提供节点详情时保留 object_id，避免用 mock 标题冒充真实实体。
export function retrievalGraph(snapshot: ResolvedSnapshot) {
  const nodes = new Map<string, { id: string; name: string }>();
  const edges = new Map<string, { source: string; target: string; relation: string }>();
  const bundle = snapshot.retrieval_bundle;
  const refs = [...bundle.rule_refs, ...bundle.support_case_refs,
    ...bundle.limiting_case_refs, ...bundle.boundary_case_refs];
  for (const ref of refs) {
    for (const path of ref.graph_paths ?? []) {
      if (path.node_ids.length < 2 || path.edge_types.length !== path.node_ids.length - 1
          || path.node_ids.some((id) => !id) || path.edge_types.some((type) => !type)) continue;
      for (const id of path.node_ids) {
        nodes.set(id, { id, name: snapshot.rules[id]?.title ?? snapshot.cases[id]?.title ?? id });
      }
      path.edge_types.forEach((relation, i) => {
        const source = path.node_ids[i];
        const target = path.node_ids[i + 1];
        edges.set(JSON.stringify([source, relation, target]), { source, target, relation });
      });
    }
  }
  return { nodes: [...nodes.values()], edges: [...edges.values()] };
}
