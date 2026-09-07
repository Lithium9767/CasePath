"""事务故障注入；真实 Neo4j 的重复导入检查由可选集成测试完成。"""

import os

import pytest

from casepath.adapters.infrastructure.neo4j_writer import publish
from casepath.ingestion.graph.projection import GraphProjection


def bundle():
    graph = GraphProjection()
    a = graph.node("Case", "test.case", {"title": "测试案例"})
    b = graph.node("Provision", "test.provision", {"text": "测试法条"})
    graph.edge(a, "CITES_PROVISION", b)
    return graph.bundle({"fixture": "test"})


class Result:
    def __init__(self, count=0):
        self.count = count

    def consume(self):
        return None

    def single(self):
        return {"count": self.count}


class Driver:
    def __init__(self, fail=False):
        self.fail = fail
        self.committed = False
        self.rolled_back = False
        self.queries = []

    def session(self, **kwargs):
        return self

    def begin_transaction(self, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *args):
        if exc_type:
            self.rolled_back = True

    def commit(self):
        self.committed = True

    def run(self, query, **params):
        self.queries.append(query)
        if self.fail and "MERGE (a)-" in query:
            raise RuntimeError("simulated relationship failure")
        return Result(2 if "count(n)" in query else 1)


def test_publish_failure_never_activates_release():
    driver = Driver(fail=True)
    with pytest.raises(RuntimeError, match="simulated"):
        publish(driver, "neo4j", bundle())
    assert driver.rolled_back and not driver.committed
    assert not any("active_release_id" in q for q in driver.queries)


def test_publish_verifies_counts_then_activates():
    driver = Driver()
    result = publish(driver, "neo4j", bundle())
    assert driver.committed and result["nodes"] == 2
    assert "active_release_id" in driver.queries[-1]
    assert not any("DELETE" in q for q in driver.queries)


@pytest.mark.skipif(
    not os.getenv("CASEPATH_KG_TEST_DATABASE"),
    reason="dedicated Neo4j KG test database not configured",
)
def test_real_neo4j_idempotence():
    # 显式使用独立测试数据库；测试图保留在该库，不碰生产活动图。
    from neo4j import GraphDatabase

    with GraphDatabase.driver(
        os.environ["CASEPATH_NEO4J_URI"],
        auth=(os.environ["CASEPATH_NEO4J_WRITE_USER"], os.environ["CASEPATH_NEO4J_WRITE_PASSWORD"]),
    ) as driver:
        database = os.environ["CASEPATH_KG_TEST_DATABASE"]
        first = publish(driver, database, bundle())
        assert publish(driver, database, bundle()) == first
        with driver.session(database=database) as session:
            value = session.run(
                "MATCH (c:CasePathCatalog {name:'casepath'}) RETURN c.active_release_id AS id"
            ).single()["id"]
            assert value == first["release_id"]
