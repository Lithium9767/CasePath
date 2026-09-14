import copy
import json
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from casepath.adapters.retrieval.graph_navigator import MemoryGraphNavigator
from casepath.adapters.retrieval.retrieval_catalog import (
    CaseDocument,
    MemoryHybridSearch,
    RetrievalCatalog,
    RuleDocument,
)
from casepath.evaluation import relation_coverage as c1
from casepath.evaluation.ac_retrieval import EvaluationQuery
from casepath.evaluation.metrics import score_ranking


def _write(path, value, *, jsonl=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        "\n".join(json.dumps(row, ensure_ascii=False) for row in value)
        if jsonl
        else json.dumps(value, ensure_ascii=False)
    )
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


@pytest.fixture
def source_inputs(tmp_path, monkeypatch):
    source = [
        {
            "uniqid": str(UUID(int=index + 1)),
            "input": f"原始事实{index}",
            "relation": {" 借款关系 ": [], "买卖关系": []},
        }
        for index in range(4)
    ]
    paths = ("inputs/legacy.json", "inputs/expanded.json")
    _write(tmp_path / paths[0], source[:2])
    _write(tmp_path / paths[1], [{"sample": row} for row in source[2:]])
    _write(
        tmp_path / "canonical/cases/cases_l1.jsonl",
        [
            {
                "case_id": "lexrel." + row["uniqid"],
                "facts_text": row["input"],
                "title": "完全不相关且不应被当标签的标题",
                "keywords": ["别的关系"],
            }
            for row in source
        ],
        jsonl=True,
    )

    def repin():
        monkeypatch.setattr(
            c1,
            "_SOURCE_PINS",
            tuple(
                (relative, c1._lf_sha(tmp_path / relative), 2, bool(index))
                for index, relative in enumerate(paths)
            ),
        )

    repin()
    return tmp_path, source, repin


def test_scope_labels_come_from_original_ids_not_metadata(source_inputs):
    data, source, _ = source_inputs
    labels = c1.source_labels(data)
    assert set(labels) == {"lexrel." + row["uniqid"] for row in source}
    assert all(value == frozenset({"借款关系", "买卖关系"}) for value in labels.values())


def test_input_hash_changes_are_rejected(source_inputs):
    data, _, _ = source_inputs
    path = data / "inputs/legacy.json"
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="source hash"):
        c1.source_labels(data)


@pytest.mark.parametrize(
    "damage",
    [
        "duplicate_uuid",
        "unknown_uuid",
        "empty_facts",
        "wrong_facts",
        "empty_labels",
        "invalid_uuid",
        "wrong_count",
    ],
)
def test_invalid_source_coverage_is_rejected_even_with_updated_pin(source_inputs, damage):
    data, source, repin = source_inputs
    rows = json.loads((data / "inputs/expanded.json").read_text(encoding="utf-8"))
    row = rows[0]["sample"]
    if damage == "duplicate_uuid":
        row["uniqid"] = source[0]["uniqid"]
    elif damage == "unknown_uuid":
        row["uniqid"] = str(UUID(int=100))
    elif damage == "empty_facts":
        row["input"] = " "
    elif damage == "wrong_facts":
        row["input"] = "不属于这个原始编号的事实"
    elif damage == "empty_labels":
        row["relation"] = {}
    elif damage == "invalid_uuid":
        row["uniqid"] = "wrong"
    else:
        rows.pop()
    _write(data / "inputs/expanded.json", rows)
    repin()
    with pytest.raises(ValueError):
        c1.source_labels(data)


def test_duplicate_canonical_case_ids_are_rejected(source_inputs):
    data, _, _ = source_inputs
    path = data / "canonical/cases/cases_l1.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    _write(path, [*rows, rows[0]], jsonl=True)
    with pytest.raises(ValueError, match="duplicate canonical"):
        c1.source_labels(data)


def _case(key, text):
    return CaseDocument(key, "标题", text, (), (), (), (), (), (), (), (), (), ())


def _catalog():
    rule = RuleDocument("rule.test", "规则", (), (), (), (), (), (), (), "相关事实", "L1")
    cases = {"in": _case("in", "相关事实"), "out": _case("out", "相关事实 相关事实")}
    return RetrievalCatalog(
        "release.test",
        "index.test",
        "none",
        {"rule.test": rule},
        cases,
        {},
        {},
        {},
        case_records={"in": object(), "out": object()},
    )


def test_scope_happens_before_bm25_and_graph_construction():
    original = _catalog()
    scoped = c1.scope_catalog(original, {"in": frozenset({"relation"})})
    assert scoped.rules is original.rules
    assert scoped.conditions is original.conditions
    assert scoped.index_version != original.index_version
    assert set(scoped.cases) == {"in"}
    assert set(scoped.case_records) == {"in"}
    assert set(original.cases) == {"in", "out"}
    search = MemoryHybridSearch(scoped)
    navigator = MemoryGraphNavigator(scoped)
    assert search.case_index.ids == ["in"]
    assert navigator.catalog is scoped
    assert set(navigator.catalog.cases) == {"in"}
    # The stronger out-of-scope text never occupies a search slot to be filtered later.
    assert [
        ref.object_id
        for ref in search.search("相关事实", target="case", context=scoped.context(), top_k=1)
    ] == ["in"]


@pytest.mark.parametrize("labels", [{}, {"missing": frozenset({"relation"})}])
def test_empty_or_unknown_scope_is_rejected(labels):
    with pytest.raises(ValueError):
        c1.scope_catalog(_catalog(), labels)


@pytest.fixture
def topic_inputs(tmp_path, monkeypatch):
    topics = [
        {"query_id": f"lexrel.{i}", "query_text": f"固定主题问题{i}", "relation_type": f"关系{i}"}
        for i in range(13)
    ]
    _write(tmp_path / c1._TOPIC_PATH, topics, jsonl=True)
    monkeypatch.setattr(c1, "_TOPIC_SHA", c1._lf_sha(tmp_path / c1._TOPIC_PATH))
    labels = {
        "in": frozenset(topic["relation_type"] for topic in topics),
        "out": frozenset({"另外的关系"}),
    }
    queries = [
        EvaluationQuery(
            "C." + topic["query_id"],
            "C",
            "relation",
            topic["query_text"],
            {"in": 1},
            c1._FAMILY + ": fixed",
            ("source",),
        )
        for topic in topics
    ]
    return tmp_path, labels, queries


def test_original_topic_queries_and_qrels_are_retained_exactly(topic_inputs):
    data, labels, queries = topic_inputs
    selected, by_query = c1.relation_queries(data, queries, labels)
    assert selected == queries
    assert len(by_query) == 13


@pytest.mark.parametrize("change", ["text", "qrels", "exclusion", "missing", "duplicate", "extra"])
def test_changed_relation_questions_or_answers_are_rejected(topic_inputs, change):
    data, labels, queries = topic_inputs
    if change == "text":
        queries[0] = replace(queries[0], query_text="重写问题")
    elif change == "qrels":
        queries[0] = replace(queries[0], qrels={"out": 1})
    elif change == "exclusion":
        queries[0] = replace(queries[0], exclude_ids=("in",))
    elif change == "missing":
        queries.pop()
    elif change == "duplicate":
        queries.append(queries[0])
    else:
        queries.append(replace(queries[0], query_id="C.other"))
    with pytest.raises(ValueError):
        c1.relation_queries(data, queries, labels)


def _metrics_fixture(*, ids=None, positives=None, k=(2, 5)):
    labels = {f"case.{i}": frozenset({"relation"}) for i in range(4)}
    query = EvaluationQuery(
        "C.test",
        "C",
        "relation",
        "普通问题",
        positives if positives is not None else {key: 1 for key in labels},
        c1._FAMILY,
        ("source",),
    )
    ranking = ["case.0", "case.1"] if ids is None else ids
    result = {
        "queries": [
            {
                "query_id": query.query_id,
                "rankings": {
                    stage: {
                        "ranked_ids": list(ranking),
                        "metrics": score_ranking(ranking, query.qrels, candidate_ids=labels, k=k),
                    }
                    for stage in ("c.bm25", "c.fused", "c.pool")
                },
            }
        ]
    }
    return labels, query, result


def test_ceiling_and_precision_do_not_redefine_recall():
    labels, query, result = _metrics_fixture()
    before = copy.deepcopy(result)
    c1.add_coverage_metrics(result, [query], labels, k=(2, 5))
    ranked = result["queries"][0]["rankings"]["c.bm25"]
    assert ranked["metrics"] == before["queries"][0]["rankings"]["c.bm25"]["metrics"]
    assert ranked["metrics"]["at_k"]["2"]["recall"] == 0.5
    assert ranked["relation_at_k"]["2"]["recall_ceiling"] == 0.5
    assert ranked["relation_at_k"]["2"]["precision"] == 1
    assert ranked["relation_at_k"]["5"]["precision"] == 0.4
    assert ranked["relation_at_k"]["5"]["recall_ceiling"] == 1


def test_relation_source_coverage_is_not_positive_qrel_presence():
    labels, query, result = _metrics_fixture(ids=["case.1"], positives={"case.0": 1})
    c1.add_coverage_metrics(result, [query], labels, k=(2, 5))
    ranking = result["queries"][0]["rankings"]["c.fused"]
    assert ranking["metrics"]["at_k"]["2"]["judged_returned"] == 0
    assert ranking["metrics"]["at_k"]["2"]["unjudged_returned"] == 1
    assert ranking["relation_at_k"]["2"]["source_labeled_returned"] == 1
    assert ranking["relation_at_k"]["2"]["source_label_coverage"] == 1
    assert ranking["relation_at_k"]["2"]["unjudged_ratio"] == 0


def test_empty_ranking_coverage_is_undefined_and_empty_precision_is_zero():
    labels, query, result = _metrics_fixture(ids=[])
    c1.add_coverage_metrics(result, [query], labels, k=(2, 5))
    summary = result["relation_summary"]["c.pool"]
    assert summary["empty_ranking_queries"] == 1
    assert summary["macro_at_k"]["2"]["source_label_coverage"] is None
    assert summary["macro_at_k"]["2"]["precision"] == 0


@pytest.mark.parametrize(
    "damage",
    [
        "missing_query",
        "duplicate_query",
        "missing_stage",
        "unknown_id",
        "duplicate_id",
        "wrong_metric",
    ],
)
def test_result_corruption_cannot_be_reported_as_success(damage):
    labels, query, result = _metrics_fixture()
    ranking = result["queries"][0]["rankings"]
    if damage == "missing_query":
        result["queries"] = []
    elif damage == "duplicate_query":
        result["queries"].append(result["queries"][0])
    elif damage == "missing_stage":
        ranking.pop("c.pool")
    elif damage == "unknown_id":
        ranking["c.bm25"]["ranked_ids"].append("unknown")
    elif damage == "duplicate_id":
        ranking["c.bm25"]["ranked_ids"].append("case.0")
    else:
        ranking["c.bm25"]["metrics"]["at_k"]["2"]["recall"] = 1
    with pytest.raises(ValueError):
        c1.add_coverage_metrics(result, [query], labels, k=(2, 5))


def test_real_pinned_105_sources_and_original_13_queries_match():
    data = Path(__file__).resolve().parents[2] / "data"
    labels = c1.source_labels(data)
    assert len(labels) == 105
    rows = [
        json.loads(line)
        for line in (data / "evaluation/ac_queries.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    queries = [
        EvaluationQuery(
            **{
                **row,
                "source_refs": tuple(row["source_refs"]),
                "exclude_ids": tuple(row.get("exclude_ids", [])),
            }
        )
        for row in rows
    ]
    selected, _ = c1.relation_queries(data, queries, labels)
    assert len(selected) == 13
    assert all(set(query.qrels) <= labels.keys() for query in selected)


def _real_inputs():
    data = Path(__file__).resolve().parents[2] / "data"
    labels = c1.source_labels(data)
    rows = [
        json.loads(line)
        for line in (data / "evaluation/ac_queries.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    queries = [
        EvaluationQuery(
            **{
                **row,
                "source_refs": tuple(row["source_refs"]),
                "exclude_ids": tuple(row.get("exclude_ids", [])),
            }
        )
        for row in rows
    ]
    return data, labels, queries


def test_contract_only_selects_original_ten_topics_and_retains_all_105_candidates():
    data, labels, queries = _real_inputs()
    original_labels = copy.deepcopy(labels)
    original_qrels = {query.query_id: dict(query.qrels) for query in queries}
    all_relations, by_query = c1.relation_queries(data, queries, labels)
    selected, excluded = c1.select_query_scope(all_relations, by_query, contract_only=True)
    assert len(selected) == 10
    assert {by_query[query.query_id] for query in selected} == c1._CONTRACT_RELATIONS
    assert {row["relation_type"] for row in excluded} == {
        "夫妻关系",
        "机动车交通事故侵权责任法律关系",
        "追偿权关系",
    }
    assert labels == original_labels
    assert len(labels) == 105
    assert all(query.qrels == original_qrels[query.query_id] for query in selected)
    catalog = replace(
        _catalog(),
        cases={key: _case(key, "仅供范围验证的事实") for key in [*labels, "unlabeled.extra"]},
    )
    scoped = c1.scope_catalog(catalog, labels)
    assert set(scoped.cases) == set(labels)
    assert len(scoped.cases) == 105
    # Dropping the standalone recourse *question* does not drop recourse cases.
    recourse_ids = {key for key, value in labels.items() if "追偿权关系" in value}
    assert recourse_ids and recourse_ids <= scoped.cases.keys()
    for query in selected:
        assert {key for key in recourse_ids if by_query[query.query_id] in labels[key]} <= (
            query.qrels.keys()
        )


def test_general_thirteen_topic_scope_remains_available_without_modifying_inputs():
    data, labels, queries = _real_inputs()
    original, by_query = c1.relation_queries(data, queries, labels)
    selected, excluded = c1.select_query_scope(original, by_query, contract_only=False)
    assert selected is original
    assert len(selected) == 13
    assert excluded == []


def test_build_contract_report_passes_scoped_catalog_before_any_retrieval(monkeypatch):
    data, labels, queries = _real_inputs()
    catalog = replace(
        _catalog(),
        cases={key: _case(key, "测试事实") for key in [*labels, "unlabeled.extra"]},
        case_records={},
    )
    monkeypatch.setattr(c1, "load_canonical_catalog", lambda root: catalog)
    monkeypatch.setattr(c1, "facts_only_catalog", lambda root, current: current)
    monkeypatch.setattr(c1, "load_queries", lambda path, current: queries)
    calls = []

    def stub_retrieval(scoped, selected, *, k, case_han_unigram_weight, case_feedback):
        assert case_han_unigram_weight == 0.25
        assert case_feedback is False
        # This replaces the retriever only in the test; no ranking or metric is
        # written to a report artifact. It verifies the ordering of the pipeline.
        assert set(scoped.cases) == set(labels)
        assert len(scoped.cases) == 105
        assert len(selected) == 10
        assert scoped.rules is catalog.rules
        calls.append(True)
        return {
            "queries": [
                {
                    "query_id": query.query_id,
                    "rankings": {
                        stage: {
                            "ranked_ids": [],
                            "metrics": score_ranking([], query.qrels, candidate_ids=labels, k=k),
                        }
                        for stage in ("c.bm25", "c.fused", "c.pool")
                    },
                }
                for query in selected
            ],
            "summary": {},
        }

    monkeypatch.setattr(c1, "evaluate_queries", stub_retrieval)
    report = c1.build_report(data, contract_only=True)
    assert calls == [True]
    assert report["query_scope"] == "contract_relation_types"
    assert len(report["excluded_topics"]) == 3
    assert report["candidate_counts"]["cases"] == 105
    assert "NOT an algorithm gain" in report["comparison_policy"]
    assert len(report["queries"]) == 10
    assert all(row["relation_type"] in c1._CONTRACT_RELATIONS for row in report["queries"])


def test_cli_contract_scope_is_explicit(monkeypatch, tmp_path):
    seen = []

    def stub_report(data_root, queries_path, *, k, contract_only, case_han_unigram_weight, case_feedback):
        assert case_han_unigram_weight == 0.25
        assert case_feedback is False
        seen.append(contract_only)
        return {
            "task": "relation_type_retrieval",
            "candidate_counts": {"cases": 105},
            "queries": [],
            "summary": {},
            "relation_summary": {},
        }

    monkeypatch.setattr(c1, "build_report", stub_report)
    monkeypatch.setattr(c1, "write_report", lambda *args, **kwargs: None)
    assert (
        c1.main(
            [
                "--data-root",
                str(tmp_path),
                "--contract-only",
                "--output",
                str(tmp_path / "reports/c1.json"),
            ]
        )
        == 0
    )
    assert seen == [True]
