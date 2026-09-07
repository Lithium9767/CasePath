from types import SimpleNamespace

import pytest

from casepath.adapters.infrastructure.neo4j_gateway import Neo4jGraphGateway
from casepath.application.errors import GraphUnavailable


class FakeRecord:
    def __init__(self, payload):
        self.payload = payload

    def data(self):
        return self.payload


class FakeDriver:
    def __init__(self):
        self.available = True
        self.closed = False
        self.calls = []

    def verify_connectivity(self):
        if not self.available:
            raise RuntimeError("connection detail must not escape")

    def execute_query(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if not self.available:
            raise TimeoutError("server address must not escape")
        return SimpleNamespace(records=[FakeRecord({"rule_id": "rule.1"})])

    def close(self):
        self.closed = True


def build_gateway(driver):
    return Neo4jGraphGateway(
        driver,
        database="neo4j",
        default_timeout_seconds=12,
        query_factory=lambda text, timeout: (text, timeout),
        read_routing="read",
    )


def test_read_query_uses_parameters_database_routing_and_timeout():
    driver = FakeDriver()
    gateway = build_gateway(driver)
    rows = gateway.execute_read("MATCH (n) RETURN n.id AS id", {"limit": 3})
    assert rows == [{"rule_id": "rule.1"}]
    query, options = driver.calls[0]
    assert query == ("MATCH (n) RETURN n.id AS id", 12)
    assert options == {
        "parameters_": {"limit": 3},
        "database_": "neo4j",
        "routing_": "read",
    }


def test_graph_errors_are_sanitized_and_close_is_idempotent():
    driver = FakeDriver()
    gateway = build_gateway(driver)
    driver.available = False
    with pytest.raises(GraphUnavailable, match="连接不可用") as health_error:
        gateway.verify_connectivity()
    assert "connection detail" not in str(health_error.value)
    with pytest.raises(GraphUnavailable, match="只读查询失败") as query_error:
        gateway.execute_read("RETURN 1", {})
    assert "server address" not in str(query_error.value)
    gateway.close()
    gateway.close()
    assert driver.closed


@pytest.mark.parametrize(("query", "timeout"), [(" ", None), ("RETURN 1", 0)])
def test_graph_rejects_invalid_local_input(query, timeout):
    with pytest.raises(ValueError):
        build_gateway(FakeDriver()).execute_read(query, {}, timeout_seconds=timeout)
