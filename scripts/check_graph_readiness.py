"""只读检查 canonical、派生图、向量索引和 Neo4j 活动发布；不导入、不调用模型。"""

import json
from collections import Counter
from types import SimpleNamespace

from casepath.adapters.retrieval.retrieval_catalog import load_canonical_catalog
from casepath.adapters.retrieval.vector_search import VectorIndex
from casepath.bootstrap import _build_graph_gateway
from casepath.config import RuntimeSettings
from casepath.config.embedding import EmbeddingSettings
from casepath.ingestion.graph.projection import load_projection, report
from casepath.ports.embedding import EmbeddingSpec


def inspect():
    settings = RuntimeSettings.from_env()
    root = settings.data_root
    projection = load_projection(root)  # 包含合同、外键、原文来源与发布哈希校验。
    catalog = load_canonical_catalog(root)
    if catalog.release_id != projection["release_id"]:
        raise ValueError("数据在检查期间改变，请重新检查")
    cases = list(catalog.case_records.values())
    explicit = {"SATISFIED", "NOT_SATISFIED", "CONFLICTING"}
    profile = {
        "case_count": len(cases),
        "maturity": dict(Counter(case.maturity.value for case in cases)),
        "condition_statuses": dict(
            Counter(a.status.value for case in cases for a in case.condition_findings)
        ),
        "cases_with_explicit_condition": sum(
            any(a.status.value in explicit for a in case.condition_findings) for case in cases
        ),
        "cases_with_findings": sum(bool(case.findings) for case in cases),
        "cases_with_reasoning": sum(bool(case.reasoning_steps) for case in cases),
        "cases_with_decisions": sum(bool(case.decisions) for case in cases),
        "cases_with_applied_rules": sum(
            any(step.applied_rule_ids for step in case.reasoning_steps) for case in cases
        ),
        "cases_with_verified_assessments": sum(
            any(a.human_verified for a in case.condition_findings) for case in cases
        ),
    }
    preview = json.loads((root / "derived/graph/preview.json").read_text("utf-8"))
    output = {
        "local_graph": report(projection),
        "profile": profile,
        "preview_matches_projection": preview == projection,
    }
    embedding = EmbeddingSettings.from_env()
    vectors = VectorIndex.load(embedding.index_path).validate(
        catalog,
        SimpleNamespace(
            spec=EmbeddingSpec(embedding.model, embedding.revision, embedding.dimensions)
        ),
    )
    output["validated_vectors"] = {key: len(value) for key, value in vectors.items()}
    gateway = _build_graph_gateway(settings)
    try:
        active = gateway.execute_read(
            "MATCH (c:CasePathCatalog {name:'casepath'}) RETURN c.active_release_id AS release", {}
        )
        output["active_releases"] = active
        release = projection["release_id"]
        nodes = gateway.execute_read(
            "MATCH (n:CasePathEntity {release_id:$release}) UNWIND labels(n) AS label WITH n,label WHERE label <> 'CasePathEntity' RETURN label,count(n) AS count",
            {"release": release},
        )
        edges = gateway.execute_read(
            "MATCH (:CasePathEntity {release_id:$release})-[r]->(:CasePathEntity {release_id:$release}) RETURN type(r) AS type,count(r) AS count",
            {"release": release},
        )
        integrity = gateway.execute_read(
            "MATCH (n:CasePathEntity {release_id:$release}) RETURN count(n)-count(DISTINCT n.key) AS duplicate_keys",
            {"release": release},
        )
        crossing = gateway.execute_read(
            "MATCH (a:CasePathEntity {release_id:$release})-[r]->(b) WHERE b.release_id IS NULL OR b.release_id <> $release OR r.release_id IS NULL OR r.release_id <> $release RETURN count(r) AS mismatched_edges",
            {"release": release},
        )
        marker = gateway.execute_read(
            "MATCH (r:CasePathRelease {release_id:$release}) RETURN r.input_hashes_json AS hashes",
            {"release": release},
        )
        output["neo4j"] = {
            "active_matches": len(active) == 1 and active[0]["release"] == release,
            "node_counts_match": {r["label"]: r["count"] for r in nodes}
            == report(projection)["node_counts"],
            "edge_counts_match": {r["type"]: r["count"] for r in edges}
            == report(projection)["edge_counts"],
            "input_hashes_match": len(marker) == 1
            and json.loads(marker[0]["hashes"]) == projection["input_hashes"],
            "duplicate_keys": integrity[0]["duplicate_keys"],
            "mismatched_edges": crossing[0]["mismatched_edges"],
        }
    finally:
        gateway.close()
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return output


if __name__ == "__main__":
    inspect()
