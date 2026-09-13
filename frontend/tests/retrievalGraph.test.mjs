import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

// 使用已有编译器测试纯数据函数，不调用模型。
const source = readFileSync(new URL('../src/utils/retrievalGraph.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022,
} }).outputText;
const { retrievalGraph } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const snapshot = (paths) => ({ rules: {}, cases: {}, retrieval_bundle: {
  rule_refs: [{ object_id: 'inheritance.rule', graph_paths: paths }],
  support_case_refs: [], limiting_case_refs: [], boundary_case_refs: [],
} });
test('empty paths never invent mock nodes', () => {
  assert.deepEqual(retrievalGraph(snapshot([])), { nodes: [], edges: [] });
});
test('preserves real direction and deduplicates', () => {
  const path = { node_ids: ['rule', 'group', 'condition'], edge_types: ['HAS_GROUP', 'HAS_MEMBER'] };
  const result = retrievalGraph(snapshot([path, path]));
  assert.equal(result.nodes.length, 3);
  assert.deepEqual(result.edges, [
    { source: 'rule', target: 'group', relation: 'HAS_GROUP' },
    { source: 'group', target: 'condition', relation: 'HAS_MEMBER' },
  ]);
});
test('rejects malformed paths', () => {
  assert.equal(retrievalGraph(snapshot([{ node_ids: ['a', 'b'], edge_types: [] }])).edges.length, 0);
});
test('different rounds do not retain previous nodes', () => {
  retrievalGraph(snapshot([{ node_ids: ['old', 'x'], edge_types: ['BASED_ON'] }]));
  assert.deepEqual(retrievalGraph(snapshot([{ node_ids: ['new', 'y'], edge_types: ['ABOUT'] }])).nodes.map((n) => n.id), ['new', 'y']);
});
