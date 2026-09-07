"""验收：P2/P3 投影、检索目录与 P4 图路径一致；有凭据时再验收 Neo4j 导入和只读查询。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from casepath.adapters.infrastructure.neo4j_gateway import Neo4jGraphGateway
from casepath.adapters.retrieval.graph_navigator import (
    MemoryGraphNavigator,
    Neo4jGraphNavigator,
)
from casepath.adapters.retrieval.hybrid_cypher import HybridCypherCaseRetriever
from casepath.adapters.retrieval.retrieval_catalog import (
    bind_context,
    current_round,
    load_canonical_catalog,
)
from casepath.contracts import QueryState, ScoredReference
from casepath.ingestion.graph.projection import load_projection, report

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TARGET_RULE = "rule.service_contract.termination_refund.v1"
DEEP_CASE = "72XyEXKWpSmjK9JWf7MckMNbffcrh9paQ89GI5cnVy0%3D"


def _edge_index(bundle: dict) -> set[tuple[str, str, str]]:
    keys = {node["key"]: node["object_id"] for node in bundle["nodes"]}
    out = set()
    for edge in bundle["edges"]:
        out.add((keys[edge["source"]], edge["type"], keys[edge["target"]]))
    return out


def _assert_paths(hits, edges: set[tuple[str, str, str]], label: str) -> None:
    if not hits:
        raise SystemExit(f"{label}: P4 returned no graph hits")
    for hit in hits:
        nodes = hit.path_nodes
        rels = hit.path_edges
        if len(nodes) != len(rels) + 1:
            raise SystemExit(f"{label}: malformed path {nodes} {rels}")
        for source, rel, target in zip(nodes[:-1], rels, nodes[1:], strict=True):
            if (source, rel, target) not in edges:
                raise SystemExit(f"{label}: path not in projection {source}-{rel}->{target}")
    print(f"{label}: {len(hits)} hits, all paths exist in projection")


def verify_offline() -> dict:
    bundle = load_projection(DATA)
    catalog = load_canonical_catalog(DATA)
    summary = report(bundle)
    if catalog.release_id != bundle["release_id"]:
        raise SystemExit("catalog.release_id != projection.release_id")
    context = catalog.context()
    edges = _edge_index(bundle)
    navigator = MemoryGraphNavigator(catalog)
    rule = catalog.rules[TARGET_RULE]
    cond_hits = navigator.cases_by_conditions(context, list(rule.condition_ids))
    apply_hits = navigator.cases_applying_rules(context, [TARGET_RULE])
    cite_hits = navigator.cases_citing_provisions(context, [TARGET_RULE])
    _assert_paths(cond_hits, edges, "cases_by_conditions")
    _assert_paths(apply_hits, edges, "cases_applying_rules")
    _assert_paths(cite_hits, edges, "cases_citing_provisions")
    if DEEP_CASE not in {hit.case_id for hit in cond_hits}:
        raise SystemExit("deep case missing from condition path")
    if DEEP_CASE not in {hit.case_id for hit in apply_hits}:
        raise SystemExit("deep case missing from APPLIES path")

    class EmptySearch:
        def search(self, *args, **kwargs):
            return []

    token = bind_context(context)
    try:
        retriever = HybridCypherCaseRetriever(
            navigator, EmptySearch(), catalog=catalog, navigator=navigator
        )
        bundle_out = retriever.retrieve(
            QueryState(session_id="verify-graph-release", initial_query="健身卡办卡后教练未上课，能否解除并退费"),
            [ScoredReference(object_id=TARGET_RULE, score=1.0)],
        )
    finally:
        from casepath.adapters.retrieval.retrieval_catalog import reset_context

        reset_context(token)
    recovered = {
        item.object_id
        for item in (
            *bundle_out.support_case_refs,
            *bundle_out.limiting_case_refs,
            *bundle_out.boundary_case_refs,
        )
    }
    evidence = current_round()
    fused = set(evidence.fused_case_ids) if evidence is not None else set()
    graph_only = set(evidence.graph_only_ids) if evidence is not None else set()
    if DEEP_CASE not in fused:
        raise SystemExit("P4 case retriever did not recover deep case from graph paths")
    if DEEP_CASE not in graph_only:
        raise SystemExit("deep case was not a graph-only hit (BM25 channel leaked)")
    role = (
        "support"
        if DEEP_CASE in {item.object_id for item in bundle_out.support_case_refs}
        else "limiting"
        if DEEP_CASE in {item.object_id for item in bundle_out.limiting_case_refs}
        else "boundary"
        if DEEP_CASE in recovered
        else "undetermined"
    )
    print(
        f"P4 graph-only retrieve recovered deep case; role={role}; "
        f"release={catalog.release_id}"
    )
    return summary


def _write_env() -> tuple[str, str, str, str] | None:
    uri = os.getenv("CASEPATH_NEO4J_URI")
    user = os.getenv("CASEPATH_NEO4J_WRITE_USER")
    password = os.getenv("CASEPATH_NEO4J_WRITE_PASSWORD")
    database = os.getenv("CASEPATH_NEO4J_DATABASE", "neo4j")
    if uri and user and password:
        return uri, user, password, database
    return None


def _read_env() -> tuple[str, str, str, str] | None:
    uri = os.getenv("CASEPATH_NEO4J_URI")
    user = os.getenv("CASEPATH_NEO4J_USER")
    password = os.getenv("CASEPATH_NEO4J_PASSWORD")
    database = os.getenv("CASEPATH_NEO4J_DATABASE", "neo4j")
    if uri and user and password:
        return uri, user, password, database
    return None


def import_if_configured(summary: dict) -> None:
    creds = _write_env()
    if creds is None:
        print("import skipped: CASEPATH_NEO4J_WRITE_USER / CASEPATH_NEO4J_WRITE_PASSWORD not set")
        return
    from neo4j import GraphDatabase

    from casepath.adapters.infrastructure.neo4j_writer import publish

    uri, user, password, database = creds
    bundle = load_projection(DATA)
    with GraphDatabase.driver(uri, auth=(user, password), connection_timeout=20) as driver:
        driver.verify_connectivity()
        published = publish(driver, database, bundle, timeout=300)
    if published["release_id"] != summary["release_id"]:
        raise SystemExit("imported release_id does not match local projection")
    print("imported", published["release_id"], "nodes", published["nodes"])


def verify_neo4j_read(summary: dict) -> None:
    creds = _read_env()
    if creds is None:
        print("read skipped: CASEPATH_NEO4J_USER / CASEPATH_NEO4J_PASSWORD not set")
        return
    uri, user, password, database = creds
    gateway = Neo4jGraphGateway.connect(
        uri=uri,
        user=user,
        password=password,
        database=database,
        connection_timeout_seconds=20,
        query_timeout_seconds=30,
        max_retry_seconds=8,
    )
    try:
        gateway.verify_connectivity()
        active = gateway.execute_read(
            "MATCH (c:CasePathCatalog {name:'casepath'}) RETURN c.active_release_id AS release_id",
            {},
        )
        if not active or active[0]["release_id"] != summary["release_id"]:
            raise SystemExit(
                f"active_release_id {active} != local {summary['release_id']}"
            )
        catalog = load_canonical_catalog(DATA)
        context = catalog.context()
        navigator = Neo4jGraphNavigator(gateway)
        rule = catalog.rules[TARGET_RULE]
        cond_hits = navigator.cases_by_conditions(context, list(rule.condition_ids))
        apply_hits = navigator.cases_applying_rules(context, [TARGET_RULE])
        cite_hits = navigator.cases_citing_provisions(context, [TARGET_RULE])
        if DEEP_CASE not in {hit.case_id for hit in cond_hits}:
            raise SystemExit("Neo4j condition path missed deep case")
        if DEEP_CASE not in {hit.case_id for hit in apply_hits}:
            raise SystemExit("Neo4j APPLIES path missed deep case")
        for hit in [*cond_hits, *apply_hits, *cite_hits]:
            for source, rel, target in zip(hit.path_nodes[:-1], hit.path_edges, hit.path_nodes[1:], strict=True):
                rows = gateway.execute_read(
                    "MATCH (a:CasePathEntity {release_id:$release_id, object_id:$source})"
                    "-[r]->(b:CasePathEntity {release_id:$release_id, object_id:$target}) "
                    "WHERE type(r)=$rel RETURN count(r) AS n",
                    {
                        "release_id": context.release_id,
                        "source": source,
                        "target": target,
                        "rel": rel,
                    },
                )
                if not rows or int(rows[0]["n"]) < 1:
                    raise SystemExit(f"Neo4j missing path {source}-{rel}->{target}")
        who = gateway.execute_read("SHOW CURRENT USER YIELD user RETURN user", {})
        if not who or who[0].get("user") != user:
            raise SystemExit("online query did not use the configured read account")
        token = bind_context(context)
        try:

            class EmptySearch:
                def search(self, *args, **kwargs):
                    return []

            retriever = HybridCypherCaseRetriever(
                navigator, EmptySearch(), catalog=catalog, navigator=navigator
            )
            bundle_out = retriever.retrieve(
                QueryState(
                    session_id="verify-graph-release-neo4j",
                    initial_query="健身卡办卡后教练未上课，能否解除并退费",
                ),
                [ScoredReference(object_id=TARGET_RULE, score=1.0)],
            )
            evidence = current_round()
        finally:
            from casepath.adapters.retrieval.retrieval_catalog import reset_context

            reset_context(token)
        fused = set(evidence.fused_case_ids) if evidence is not None else set()
        if DEEP_CASE not in fused:
            raise SystemExit("P4 Neo4j retrieve missed deep case")
        write = _write_env()
        if write is not None and write[1] == user and write[2] == password:
            print("reader and writer are the same account; write-denial check skipped")
        else:
            _assert_reader_cannot_write(uri, user, password, database)
        print(
            "Neo4j P4 queries matched local release; "
            f"condition_hits={len(cond_hits)} apply_hits={len(apply_hits)} "
            f"cite_hits={len(cite_hits)} roles="
            f"{len(bundle_out.support_case_refs)}/{len(bundle_out.limiting_case_refs)}/"
            f"{len(bundle_out.boundary_case_refs)}"
        )
    finally:
        gateway.close()


def _assert_reader_cannot_write(uri: str, user: str, password: str, database: str) -> None:
    from neo4j import GraphDatabase
    from neo4j.exceptions import ClientError, Forbidden

    with (
        GraphDatabase.driver(uri, auth=(user, password), connection_timeout=5) as driver,
        driver.session(database=database) as session,
        session.begin_transaction() as transaction,
    ):
        # 即使误配了写权限，也只在未提交事务中探测，绝不删除其他探针节点。
        try:
            transaction.run("CREATE (n:CasePathProbe {k:1})").consume()
        except (ClientError, Forbidden):
            print("reader write denied")
            return
        finally:
            transaction.rollback()
    raise SystemExit("read account was allowed to write")


def main() -> None:
    os.chdir(ROOT)
    summary = verify_offline()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    import_if_configured(summary)
    verify_neo4j_read(summary)


if __name__ == "__main__":
    main()
