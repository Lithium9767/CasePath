"""Frozen benchmark provenance and judgment checks; never call a retriever here."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
QUERY_PATH = ROOT / "data/evaluation/ac_queries.jsonl"
MANIFEST_PATH = ROOT / "data/evaluation/ac_queries.manifest.json"


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _normalized_bytes(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


QUERIES = _load_jsonl(QUERY_PATH)
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
RULES = {
    row["rule_id"]: row
    for row in _load_jsonl(ROOT / "data/canonical/rules/rules.jsonl")
}
CASES = {
    row["case_id"]: row
    for row in _load_jsonl(ROOT / "data/canonical/cases/cases_l1.jsonl")
}


def test_query_receipt_and_candidate_counts() -> None:
    raw = _normalized_bytes(QUERY_PATH)
    receipt = MANIFEST["files"]["ac_queries.jsonl"]
    assert receipt["sha256"] == hashlib.sha256(raw).hexdigest()
    assert receipt["bytes_lf"] == len(raw)
    assert receipt["records"] == len(QUERIES) == 47
    assert len(RULES) == 776
    assert len(CASES) == 156
    assert Counter(query["module"] for query in QUERIES) == {"A": 28, "C": 19}
    assert len({query["query_id"] for query in QUERIES}) == len(QUERIES)
    assert len({query["query_text"] for query in QUERIES}) == len(QUERIES)


@pytest.mark.parametrize(
    "specification",
    MANIFEST["candidate_snapshots"] + MANIFEST["annotation_sources"],
    ids=lambda specification: specification["path"],
)
def test_source_snapshot_is_pinned(specification: dict) -> None:
    path = ROOT / specification["path"]
    raw = _normalized_bytes(path)
    assert hashlib.sha256(raw).hexdigest() == specification["sha256"]
    assert len(raw) == specification["bytes_lf"]
    records = _load_jsonl(path) if path.suffix == ".jsonl" else json.loads(raw)
    assert len(records) == specification["records"]


@pytest.mark.parametrize("query", QUERIES, ids=lambda query: query["query_id"])
def test_query_schema_and_judgments_resolve_in_actual_candidates(query: dict) -> None:
    assert set(query) == {
        "query_id", "module", "domain", "query_text", "qrels",
        "judgment_basis", "source_refs", "exclude_ids",
    }
    assert query["query_id"].startswith(query["module"] + ".")
    assert isinstance(query["query_text"], str) and query["query_text"].strip()
    assert query["domain"]
    assert len(query["source_refs"]) == len(set(query["source_refs"]))
    assert all(isinstance(ref, str) and ref for ref in query["source_refs"])
    assert query["exclude_ids"] == []
    assert query["qrels"]
    candidates = RULES if query["module"] == "A" else CASES
    assert set(query["qrels"]) <= candidates.keys()
    assert all(type(grade) is int and 0 <= grade <= 3 for grade in query["qrels"].values())
    assert any(grade > 0 for grade in query["qrels"].values())
    assert query["judgment_basis"].partition(":")[0] in MANIFEST["annotation_families"]
    assert not any(candidate_id in query["query_text"] for candidate_id in query["qrels"])
    assert not re.search(r"第[零〇一二三四五六七八九十百千万\d]+条", query["query_text"])
    assert not re.search(r"\d{16,19}|[\w.-]+@[\w.-]+", query["query_text"])


def test_a_has_four_scenarios_per_civil_code_book() -> None:
    counts = Counter(query["domain"] for query in QUERIES if query["module"] == "A")
    assert counts == {
        "总则": 4, "物权": 4, "合同": 4, "人格权": 4,
        "婚姻家庭": 4, "继承": 4, "侵权责任": 4,
    }
    assert counts == MANIFEST["counts"]["A_domains"]


@pytest.mark.parametrize(
    "query", [query for query in QUERIES if query["module"] == "A"],
    ids=lambda query: query["query_id"],
)
def test_each_a_judgment_has_bound_provision_and_source_span_evidence(query: dict) -> None:
    refs = set(query["source_refs"])
    assert "https://www.court.gov.cn/zixun/xiangqing/233181.html" in refs
    for rule_id in query["qrels"]:
        rule = RULES[rule_id]
        assert f"rule:{rule_id}" in refs
        assert rule["provisions"] and rule["source_spans"]
        assert {"provision:" + item["provision_id"] for item in rule["provisions"]} <= refs
        assert {"span:" + item["span_id"] for item in rule["source_spans"]} <= refs


def test_c_relation_judgments_are_rebuilt_from_separate_source_labels() -> None:
    legacy = json.loads((ROOT / "data/inputs/lexrel_service_contract.json").read_text("utf-8"))
    expanded = json.loads((ROOT / "data/inputs/lexrel_retrieval_cases.json").read_text("utf-8"))
    records = legacy + [row["sample"] for row in expanded]
    labels = {
        "lexrel." + row["uniqid"]: {label.strip() for label in row["relation"]}
        for row in records
    }
    assert len(labels) == 105
    topics = _load_jsonl(ROOT / "data/evaluation/case_retrieval_qrels.jsonl")
    queries = {query["query_id"]: query for query in QUERIES}
    for topic in topics:
        query = queries["C." + topic["query_id"]]
        assert query["query_text"] == topic["query_text"]
        expected = {
            case_id: 1 for case_id, annotation in labels.items()
            if topic["relation_type"] in annotation
        }
        assert query["qrels"] == expected
        assert query["judgment_basis"].startswith("weak_relation_label_non_exhaustive:")
        assert "relation:" + topic["relation_type"] in query["source_refs"]
    assert len(topics) == 13


def test_spc_scenarios_are_explicit_known_item_not_blind_gold() -> None:
    queries = [query for query in QUERIES if query["query_id"].startswith("C.spc.")]
    summaries = _load_jsonl(ROOT / "data/inputs/spc_civil_code_first_batch.jsonl")
    inputs = {row["case_id"]: row for row in summaries}
    expected_primary_ids = set(inputs)
    primary_ids = set()
    for query in queries:
        assert query["judgment_basis"].startswith("curated_known_item_silver_non_exhaustive:")
        assert "已知案例定位" in query["judgment_basis"]
        assert "https://www.court.gov.cn/zixun/xiangqing/347181.html" in query["source_refs"]
        direct = [case_id for case_id, grade in query["qrels"].items() if grade == 3]
        assert len(direct) == 1
        source = inputs[direct[0]]
        assert "source_section:" + str(source["source_section"]) in query["source_refs"]
        assert source["title"] not in query["query_text"]
        assert source["judgment_summary"] not in query["query_text"]
        assert source["case_facts_text"] != query["query_text"]
        primary_ids.update(direct)
    assert primary_ids == expected_primary_ids
    assert len(queries) == 6


def test_manifest_discloses_non_exhaustive_and_incompatible_label_semantics() -> None:
    families = MANIFEST["annotation_families"]
    assert len(families) == 3
    for family, description in families.items():
        assert description["judgments_exhaustive"] is False
        assert description["queries"] == sum(
            query["judgment_basis"].startswith(family + ":") for query in QUERIES
        )
    assert "NOT a finding" in MANIFEST["policies"]["unjudged"]
    assert "Only query_text" in MANIFEST["policies"]["runtime_input"]
    assert "separately" in MANIFEST["policies"]["metric_groups"]
    assert "not a statistically representative blinded test" in MANIFEST["policies"]["selection"]
    assert MANIFEST["counts"]["positive_judgments"] == sum(
        sum(grade > 0 for grade in query["qrels"].values()) for query in QUERIES
    )
