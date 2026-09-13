from pathlib import Path

import pytest

from casepath.ingestion.cases.ids import EXPECTED_CIVIL_CASE_COUNT, GYM_CASE_ID
from casepath.ingestion.cases.refinements import SERVICE_REFUND_COMPARISON_CASE_IDS
from casepath.ingestion.graph.projection import GraphProjection, load_projection, node_key


def test_rejects_conflicting_nodes_and_dangling_edges():
    graph = GraphProjection()
    key = graph.node("RuleCondition", "c", {"label": "a"})
    with pytest.raises(ValueError, match="conflicting"):
        graph.node("RuleCondition", "c", {"label": "b"})
    graph.edge(key, "ABOUT", "missing")
    with pytest.raises(ValueError, match="dangling"):
        graph.bundle({})


def test_canonical_two_layer_paths():
    root = Path(__file__).resolve().parents[3] / "data"
    first = load_projection(root)
    assert first == load_projection(root)
    nodes = {n["key"]: n for n in first["nodes"]}
    assert sum(n["label"] == "Case" for n in nodes.values()) == EXPECTED_CIVIL_CASE_COUNT
    assert sum(n["label"] == "RuleCondition" for n in nodes.values()) == 1928
    assert sum(n["label"] == "LegalRule" for n in nodes.values()) == 776
    assert sum(n["label"] == "Provision" for n in nodes.values()) == 1260
    assert sum(e["type"] == "CITES_PROVISION" for e in first["edges"]) == 80
    condition = node_key("RuleCondition", "cond.alternative_performance")
    assessments = [
        e["source"] for e in first["edges"] if e["type"] == "ABOUT" and e["target"] == condition
    ]
    assert assessments
    assessed_cases = {nodes[key]["properties"]["case_id"] for key in assessments}
    assert assessed_cases == set(SERVICE_REFUND_COMPARISON_CASE_IDS)
    gym_key = next(
        key for key in assessments if nodes[key]["properties"]["case_id"] == GYM_CASE_ID
    )
    assert nodes[gym_key]["properties"]["status"] == "NOT_SATISFIED"
    assert any(e["source"] == gym_key and e["type"] == "EVIDENCED_BY" for e in first["edges"])
    assert not any(e["type"] in {"SATISFIES", "REALIZES", "SUPPORTS_CASE"} for e in first["edges"])
    assert sum(e["type"] == "APPLIES" for e in first["edges"]) == 1
    assert sum(e["type"] == "INVOKES" for e in first["edges"]) == 2
