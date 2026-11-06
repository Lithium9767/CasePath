"""Production-component ablations; no gold rewriting or product mutations.

Retrieval scores are relation-label diagnostics on the deployed index, whose
text includes causes/keywords. B measures no-new-fact replay, not accuracy.
D/E measures candidate-decision sensitivity, not question quality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import subprocess
import time
from collections import defaultdict
from pathlib import Path

from fastapi.testclient import TestClient

from casepath.adapters.retrieval.hybrid_cypher import (
    HybridCypherCaseRetriever,
    HybridCypherRuleRetriever,
)
from casepath.adapters.retrieval.retrieval_catalog import (
    RuleScope,
    bind_context,
    current_round,
    reset_context,
    set_scope,
)
from casepath.adapters.runtime.request_context import p4_request_context
from casepath.api.configured import create_configured_app
from casepath.application.runtime_meta import SessionRuntimeMeta
from casepath.contracts import ComparisonBundle, QueryState, RetrievalBundle, ScoredReference

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


class SearchView:
    def __init__(self, rows, vector=True):
        self.rows, self.vector = rows, vector

    def search(self, text, *, target, context, top_k):
        return [x.model_copy(deep=True) for x in self.rows[target]
                if self.vector or 'vector' not in x.retrieval_channels]


class GraphView:
    """Read-only request cache. A graph ablation removes seed expansion too."""
    def __init__(self, delegate, enabled=True):
        self.delegate, self.enabled, self.cache = delegate, enabled, {}

    def __getattr__(self, name):
        def call(context, ids):
            if not self.enabled:
                return []
            key = (name, tuple(ids))
            if key not in self.cache:
                self.cache[key] = getattr(self.delegate, name)(context, ids)
            return self.cache[key]
        return call


class CounterModel:
    def __init__(self, delegate):
        self.delegate, self.calls = delegate, 0

    def generate_structured(self, **kwargs):
        self.calls += 1
        return self.delegate.generate_structured(**kwargs)


def scores(ids, relevant, k):
    hits = [int(x in relevant) for x in ids[:k]]
    dcg = sum(h / math.log2(i + 2) for i, h in enumerate(hits))
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(relevant))))
    return {'recall': sum(hits) / len(relevant), 'ndcg': dcg / ideal,
            'hits': sum(hits), 'relevant_n': len(relevant)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    qpath = ROOT / 'data/evaluation/case_retrieval_qrels.jsonl'
    frozen_path = ROOT / 'experiments/e_questioning/datasets/v1/frozen_samples.jsonl'
    qrels = [json.loads(x) for x in qpath.read_text(encoding='utf-8').splitlines()]
    frozen = [json.loads(x) for x in frozen_path.read_text(encoding='utf-8').splitlines()]
    results, replays, sensitivities = [], [], []
    rng = random.Random(20260914)
    with TestClient(create_configured_app()) as app:
        runtime = app.app.state.live_runtime
        deps = runtime.service.workflow.dependencies
        base_a = deps.rule_retriever.retriever
        expanded = base_a.search
        raw_search = expanded.search_backend
        if not getattr(raw_search, 'vector_enabled', False):
            raise RuntimeError('Live embedding/index required; no silent fallback')
        catalog = base_a.catalog
        dump(out / 'manifest.json', {
            'head': subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
            'release': catalog.release_id, 'index': catalog.index_version,
            'qrels_sha256': hashlib.sha256(qpath.read_bytes()).hexdigest(),
            'frozen_sha256': hashlib.sha256(frozen_path.read_bytes()).hexdigest(),
            'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'model': type(runtime.language_model).__name__,
            'graph': type(base_a.navigator).__name__,
            'seed': 20260914, 'cases': len(catalog.cases),
            'warning': 'Relation labels overlap deployed cause/keyword text. Not legal relevance gold. No rule qrels.',
        })
        for query in qrels:
            relevant_all = set(query['relevant_case_ids'])
            relevant = relevant_all & catalog.cases.keys()
            if not relevant or relevant != relevant_all:
                raise ValueError('qrels outside deployed corpus; do not silently change denominator')
            text = query['query_text']
            state = QueryState(session_id=query['query_id'], initial_query=text)
            token = bind_context(catalog.context())
            try:
                raw, ext = {}, {}
                for target, budget in [('rule',10), ('case',30)]:
                    raw[target] = raw_search.search(text, target=target, context=catalog.context(), top_k=budget)
                    ext[target] = expanded.search(text, target=target, context=catalog.context(), top_k=budget)
                graph = GraphView(base_a.navigator)
                variants = ['bm25','hybrid','hybrid_graph','full_expansion']
                rng.shuffle(variants)
                for variant in variants:
                    search = SearchView(ext if variant == 'full_expansion' else raw, variant != 'bm25')
                    nav = graph if variant in {'hybrid_graph','full_expansion'} else GraphView(base_a.navigator, False)
                    with p4_request_context(SessionRuntimeMeta()):
                        refs = HybridCypherRuleRetriever(nav, search, catalog=catalog, navigator=nav).retrieve(state)
                        bundle = HybridCypherCaseRetriever(nav, search, catalog=catalog, navigator=nav).retrieve(state, refs)
                        evidence = current_round()
                        ids = list(evidence.fused_case_ids)
                        record = {'query_id': query['query_id'], 'variant': variant,
                                  'rule_ids': [r.object_id for r in refs], 'ranked_case_ids': ids,
                                  'graph_only': sorted(evidence.graph_only_ids),
                                  'roles': len(bundle.support_case_refs)+len(bundle.limiting_case_refs)+len(bundle.boundary_case_refs),
                                  'metrics': {str(k): scores(ids,relevant,k) for k in (5,10,20)}}
                        results.append(record)
                dump(out / 'retrieval.json', results)
                print('retrieval', query['query_id'], flush=True)
            finally:
                reset_context(token)

        # Input states are not accepted as quality gold. Read only to measure
        # production candidate selection with and without D and A scope.
        policy = deps.question_policy
        for row in frozen:
            state = QueryState.model_validate(row['query_state'])
            bundle = RetrievalBundle.model_validate(row['retrieval_bundle'])
            comparison = ComparisonBundle.model_validate(row['comparison_bundle'])
            meta = SessionRuntimeMeta.model_validate(row['runtime_meta'])
            scope = RuleScope(**row['rule_scope'])
            collect = lambda comp, sc, state=state, bundle=bundle, meta=meta: policy._collect_candidate_conditions(
                state,bundle,comp,sc,set(meta.unable_condition_ids),set(meta.declined_condition_ids))
            full = collect(comparison,scope)
            no_d = collect(ComparisonBundle(),scope)
            no_scope = collect(comparison,None)
            sensitivities.append({'sample_id':row['sample_id'], 'full':full, 'without_D':no_d,
                                  'without_A_scope':no_scope, 'D_changes_candidates':full!=no_d,
                                  'scope_changes_candidates':full!=no_scope,
                                  'D_explicit_case_n':sum(sum(c.status_counts.values()) for c in comparison.condition_comparisons)})
        dump(out/'decision_sensitivity.json', sensitivities)

        # Same projected state, no new fact. Remove only the successful ledger
        # in control; corrections and all other metadata stay unchanged.
        projector = deps.condition_projector
        original = projector.language_model
        try:
            for row in frozen[:6]:
                state = QueryState.model_validate(row['query_state'])
                refs = [ScoredReference.model_validate(x) for x in row['retrieval_bundle']['rule_refs']]
                variants = ['ledger','without_ledger']
                rng.shuffle(variants)
                for variant in variants:
                    meta = SessionRuntimeMeta.model_validate(row['runtime_meta'])
                    if variant == 'without_ledger':
                        meta.projection_ledger = []
                    model = CounterModel(original)
                    projector.language_model = model
                    started = time.perf_counter()
                    try:
                        with p4_request_context(meta):
                            set_scope(RuleScope(**row['rule_scope']))
                            updated = projector.project(state.model_copy(deep=True),refs)
                        before = {c.condition_id:c.status.value for c in state.condition_states}
                        after = {c.condition_id:c.status.value for c in updated.condition_states}
                        result = {'changed_states':{k:[before.get(k),v] for k,v in after.items() if before.get(k)!=v},'error':None}
                    except Exception as exc:  # noqa: BLE001 - retain failed experimental trials
                        result = {'changed_states':None,'error':type(exc).__name__}
                    replays.append({'sample_id':row['sample_id'],'variant':variant,
                                    'calls':model.calls,'seconds':time.perf_counter()-started,**result})
                    dump(out/'projection_replay.json',replays)
                print('B replay',row['sample_id'],flush=True)
        finally:
            projector.language_model = original
    grouped = defaultdict(list)
    for row in results:
        grouped[row['variant']].append(row)
    summary = {variant:{str(k):{metric:sum(r['metrics'][str(k)][metric] for r in rows)/len(rows)
                               for metric in ('recall','ndcg')} for k in (5,10,20)}
               for variant,rows in grouped.items()}
    dump(out/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
