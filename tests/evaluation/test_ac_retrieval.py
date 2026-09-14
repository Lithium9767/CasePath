import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from casepath.adapters.retrieval.retrieval_catalog import (
    CaseDocument,
    RetrievalCatalog,
    RuleDocument,
    current_context,
    current_round,
    current_scope,
)
from casepath.application.runtime_meta import current_runtime_meta
from casepath.evaluation.ac_retrieval import (
    EvaluationQuery,
    _benchmark_manifest,
    _state,
    build_report,
    evaluate_predictions,
    evaluate_queries,
    facts_only_catalog,
    load_queries,
    write_report,
)


@pytest.fixture
def catalog():
    rules = {
        f"rule.{index:02d}": RuleDocument(
            rule_id=f"rule.{index:02d}",
            title="测试",
            claim_types=(),
            condition_ids=(),
            core_ids=(),
            diagnostic_ids=(),
            groups=(),
            provision_ids=(),
            span_ids=(),
            search_text="健身充值服务合同",
            maturity="L1",
        )
        for index in range(25)
    }
    cases = {
        f"case.{index}": CaseDocument(
            case_id=f"case.{index}",
            title="不应进入事实索引的标签",
            search_text="健身充值服务合同",
            cited_provision_ids=(),
            applied_rule_ids=(),
            invoked_rule_ids=(),
            assessments=(),
            claims=(),
            decisions=(),
            reasoning=(),
            span_ids=(),
            finding_predicates=(),
            finding_ids=(),
        )
        for index in range(5)
    }
    return RetrievalCatalog("release.eval", "index.eval", "none", rules, cases, {}, {}, {})


def _row(module="A"):
    return {
        "query_id": f"eval.{module}.1",
        "module": module,
        "domain": "服务合同",
        "query_text": "健身充值服务合同",
        "qrels": {"rule.00" if module == "A" else "case.0": 3},
        "judgment_basis": "silver",
        "source_refs": ["local:test"],
    }


def _write(path: Path, rows):
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _query(module="A"):
    row = _row(module)
    return EvaluationQuery(**{**row, "source_refs": tuple(row["source_refs"])})


def test_feedback_candidate_keeps_named_bm25_control_plain(monkeypatch, catalog):
    from casepath.evaluation import ac_retrieval as runner

    original = runner.MemoryHybridSearch
    constructed = []

    class ObservedSearch(original):
        def __init__(self, catalog, **kwargs):
            super().__init__(catalog, **kwargs)
            self.case_calls = 0
            constructed.append(self)

        def search(self, text, *, target, context, top_k):
            if target == "case":
                self.case_calls += 1
            return super().search(text, target=target, context=context, top_k=top_k)

    monkeypatch.setattr(runner, "MemoryHybridSearch", ObservedSearch)
    result = runner.evaluate_queries(catalog, [_query("C")], case_feedback=True)
    assert [search.case_feedback for search in constructed] == [True, False]
    assert all(search.case_calls for search in constructed)
    assert result["queries"][0]["settings"]["case_feedback"] is True


def test_loader_accepts_separate_modules_and_no_positive_queries(tmp_path, catalog):
    a, c = _row(), _row("C")
    a["qrels"] = {}
    queries = load_queries(_write(tmp_path / "queries.jsonl", [a, c]), catalog)
    assert len(queries) == 2
    assert queries[0].qrels == {}


@pytest.mark.parametrize(
    "change",
    [
        {"module": "D"},
        {"query_id": "../invalid"},
        {"query_text": " "},
        {"domain": ""},
        {"judgment_basis": ""},
        {"source_refs": []},
        {"source_refs": ["a", "a"]},
        {"qrels": {"rule.absent": 1}},
        {"qrels": {"rule.00": True}},
        {"exclude_ids": ["absent"]},
        {"extra": "silently ignored?"},
    ],
)
def test_query_validation_rejects_bad_records(tmp_path, catalog, change):
    with pytest.raises((ValueError, TypeError)):
        load_queries(_write(tmp_path / "q.jsonl", [{**_row(), **change}]), catalog)


def test_duplicate_ids_and_normalized_text_are_rejected(tmp_path, catalog):
    with pytest.raises(ValueError):
        load_queries(_write(tmp_path / "q.jsonl", [_row(), _row()]), catalog)
    second = {**_row(), "query_id": "eval.A.2", "query_text": "健 身充 值服务合同"}
    with pytest.raises(ValueError):
        load_queries(_write(tmp_path / "q.jsonl", [_row(), second]), catalog)


def test_duplicate_json_qrel_keys_are_rejected(tmp_path, catalog):
    text = json.dumps(_row()).replace('"rule.00": 3', '"rule.00": 3, "rule.00": 1')
    path = tmp_path / "q.jsonl"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_queries(path, catalog)


def test_actual_a_returns_20_not_its_default_10(catalog):
    report = evaluate_queries(catalog, [_query()], k=[5, 10, 20])
    row = report["queries"][0]
    assert row["settings"]["rule_top_k"] == 20
    assert len(row["rankings"]["a.final"]["ranked_ids"]) == 20
    assert row["rankings"]["a.final"]["metrics"]["at_k"]["20"]["recall"] == 1


def test_c_undetermined_retrieval_is_not_miscounted_as_missing(catalog):
    result = evaluate_queries(catalog, [_query("C")], k=[5, 10, 20])
    row = result["queries"][0]
    assert row["diagnostics"]["cascade_status"] == "A_B_C_completed"
    assert row["diagnostics"]["undetermined_count"] == 5
    assert all(not ids for ids in row["diagnostics"]["role_output_ids"].values())
    assert len(row["rankings"]["c.fused"]["ranked_ids"]) == 5
    assert row["rankings"]["c.fused"]["metrics"]["at_k"]["5"]["recall"] == 1
    assert set(result["summary"]) == {"c.fused", "c.pool", "c.bm25"}


def test_gold_never_enters_runtime_query_or_changes_ranking(catalog):
    query = _query("C")
    different_gold = replace(query, qrels={"case.4": 1}, source_refs=("do-not-inject",))
    assert _state(query) == _state(different_gold)
    first = evaluate_queries(catalog, [query])["queries"][0]
    other = evaluate_queries(catalog, [different_gold])["queries"][0]
    for stage in first["rankings"]:
        assert first["rankings"][stage]["ranked_ids"] == other["rankings"][stage]["ranked_ids"]


def test_each_query_context_is_restored_and_reports_are_deterministic(catalog):
    before = (current_context(), current_round(), current_scope(), current_runtime_meta())
    queries = [_query(), _query("C")]
    assert evaluate_queries(catalog, queries) == evaluate_queries(catalog, queries)
    after = (current_context(), current_round(), current_scope(), current_runtime_meta())
    assert after == before


def test_c_failure_is_not_silently_skipped(catalog, monkeypatch):
    from casepath.evaluation import ac_retrieval

    def fail(*args, **kwargs):
        raise ValueError("private case body should not enter exception summary")

    monkeypatch.setattr(ac_retrieval.HybridCypherCaseRetriever, "retrieve", fail)
    before = current_runtime_meta()
    with pytest.raises(RuntimeError, match="evaluation failed at eval.C.1; ValueError") as caught:
        evaluate_queries(catalog, [_query("C")])
    assert "private case body" not in str(caught.value)
    assert current_runtime_meta() is before


def test_empty_a_is_a_counted_cascade_failure(catalog):
    query = replace(_query("C"), query_text="zyxwv123")
    result = evaluate_queries(catalog, [query])["queries"][0]
    assert result["diagnostics"]["cascade_status"] == "empty_A_no_C_candidates"
    assert result["rankings"]["c.fused"]["metrics"]["at_k"]["5"]["recall"] == 0


def test_external_rankings_require_every_query(tmp_path, catalog):
    query = _query()
    path = _write(tmp_path / "p.jsonl", [{"query_id": query.query_id, "ranked_ids": []}])
    result = evaluate_predictions(path, catalog, [query])
    assert result["summary"]["a.external"]["scored_query_count"] == 1
    assert result["summary"]["a.external"]["macro_at_k"]["5"]["recall"] == 0
    with pytest.raises(ValueError, match="every query"):
        evaluate_predictions(path, catalog, [query, _query("C")])


@pytest.mark.parametrize("ids", [["missing"], ["rule.00", "rule.00"]])
def test_external_ranking_ids_are_strict(tmp_path, catalog, ids):
    path = _write(tmp_path / "p.jsonl", [{"query_id": "eval.A.1", "ranked_ids": ids}])
    with pytest.raises(ValueError):
        evaluate_predictions(path, catalog, [_query()])


def test_writer_is_idempotent_and_cannot_overwrite_inputs_or_other_report(tmp_path):
    data_root = tmp_path / "data"
    path = data_root / "reports" / "eval.json"
    write_report(path, {"ok": 1}, data_root=data_root)
    write_report(path, {"ok": 1}, data_root=data_root)
    with pytest.raises(ValueError, match="different existing"):
        write_report(path, {"ok": 2}, data_root=data_root)
    with pytest.raises(ValueError, match="overwrite an input"):
        write_report(path, {"ok": 1}, data_root=data_root, inputs=[path])
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": 1}


@pytest.mark.parametrize(
    "relative", ["canonical/rules/rules.json", "reports/out.jsonl", "../x.json"]
)
def test_writer_rejects_non_report_targets(tmp_path, relative):
    data_root = tmp_path / "data"
    with pytest.raises(ValueError):
        write_report(data_root / relative, {}, data_root=data_root)


def test_facts_view_changes_only_search_text_and_never_writes_canonical(tmp_path, catalog):
    path = tmp_path / "canonical/cases/cases_l1.jsonl"
    path.parent.mkdir(parents=True)
    _write(
        path,
        [
            {
                "case_id": key,
                "title": "标签秘密",
                "keywords": ["关系标注"],
                "cause": "标签案由",
                "facts_text": "消费者购买健身服务。",
                "reasoning_text": "裁判推理秘密",
                "summary": "判决结果秘密",
            }
            for key in catalog.cases
        ],
    )
    before = path.read_bytes()
    view = facts_only_catalog(tmp_path, catalog)
    assert path.read_bytes() == before
    assert view is not catalog
    assert view.index_version != catalog.index_version
    assert view.rules is catalog.rules
    for key, case in view.cases.items():
        assert case.search_text == "消费者购买健身服务。"
        assert replace(case, search_text=catalog.cases[key].search_text) == catalog.cases[key]
        assert catalog.cases[key].search_text == "健身充值服务合同"


@pytest.fixture
def report_inputs(tmp_path, catalog, monkeypatch):
    from casepath.evaluation import ac_retrieval

    data_root = tmp_path / "data"
    candidate = data_root / "canonical/candidate.jsonl"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("{}\n", encoding="utf-8", newline="\n")
    query = data_root / "evaluation/ac_queries.jsonl"
    query.parent.mkdir(parents=True)
    _write(query, [_row()])
    manifest = query.with_name("ac_queries.manifest.json")
    manifest.write_text(
        json.dumps(
            {
                "files": {
                    query.name: {
                        "sha256": hashlib.sha256(query.read_bytes()).hexdigest(),
                        "records": 1,
                    }
                },
                "candidate_snapshots": [
                    {
                        "path": "data/canonical/candidate.jsonl",
                        "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                        "records": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ac_retrieval, "_CANONICAL", ("canonical/candidate.jsonl",))
    monkeypatch.setattr(ac_retrieval, "load_canonical_catalog", lambda root: catalog)
    monkeypatch.setattr(ac_retrieval, "_implementation_hashes", lambda root: {"code": "fixed"})
    monkeypatch.setattr(ac_retrieval, "_git_info", lambda root: {"commit": "fixed", "dirty": False})
    return data_root, query, manifest, candidate


def test_build_report_validates_manifest_and_declares_canonical_risk(report_inputs):
    data_root, query, manifest, _ = report_inputs
    report = build_report(data_root, query, case_text_view="canonical")
    assert (
        report["inputs"]["benchmark_manifest"]["sha256"]
        == hashlib.sha256(manifest.read_bytes()).hexdigest()
    )
    assert report["candidate_counts"] == {"rules": 25, "cases": 5}
    assert report["case_text_view"] == "canonical"
    assert "annotation enriched" in report["retrieval_configuration"]["case_text"]
    assert any("label leakage risk" in text for text in report["limitations"])
    assert report["implementation_sha256"] == {"code": "fixed"}


def test_text_view_selection_is_explicit_without_mutating_source(report_inputs, monkeypatch):
    from casepath.evaluation import ac_retrieval

    data_root, query, _, candidate = report_inputs
    before = candidate.read_bytes()
    seen = []

    def fact_view(root, catalog):
        seen.append(root)
        return replace(
            catalog,
            index_version="facts.index",
            cases={
                key: replace(case, search_text="独立事实") for key, case in catalog.cases.items()
            },
        )

    monkeypatch.setattr(ac_retrieval, "facts_only_catalog", fact_view)
    canonical = build_report(data_root, query, case_text_view="canonical")
    assert seen == []
    facts = build_report(data_root, query)  # default is facts, not production metadata
    assert seen == [data_root.resolve()]
    assert facts["case_search_text_sha256"] != canonical["case_search_text_sha256"]
    assert facts["evaluation_index_version"] == "facts.index"
    assert candidate.read_bytes() == before


@pytest.mark.parametrize("change", ["query", "candidate", "records", "escape"])
def test_manifest_mismatch_fails_before_retrieval(report_inputs, monkeypatch, change):
    from casepath.evaluation import ac_retrieval

    data_root, query, manifest, _ = report_inputs
    item = json.loads(manifest.read_text(encoding="utf-8"))
    if change == "query":
        item["files"][query.name]["sha256"] = "wrong"
    elif change == "candidate":
        item["candidate_snapshots"][0]["sha256"] = "wrong"
    elif change == "records":
        item["candidate_snapshots"][0]["records"] = 2
    else:
        item["candidate_snapshots"][0]["path"] = "../escape.jsonl"
    manifest.write_text(json.dumps(item), encoding="utf-8")

    def forbidden(*args, **kwargs):
        pytest.fail("retrieval must not run with corrupt manifest")

    monkeypatch.setattr(ac_retrieval, "evaluate_queries", forbidden)
    with pytest.raises(ValueError):
        build_report(data_root, query, case_text_view="canonical")


def test_manifest_hash_accepts_crlf_but_report_records_exact_bytes(report_inputs):
    data_root, query, _, _ = report_inputs
    query.write_bytes(query.read_bytes().replace(b"\n", b"\r\n"))
    assert _benchmark_manifest(query, data_root, 1) is not None
    report = build_report(data_root, query, case_text_view="canonical")
    assert report["inputs"]["queries_sha256"] == hashlib.sha256(query.read_bytes()).hexdigest()


@pytest.mark.parametrize("changed_input", ["query", "canonical", "manifest", "code"])
def test_changes_during_run_prevent_successful_report(report_inputs, monkeypatch, changed_input):
    from casepath.evaluation import ac_retrieval

    data_root, query, manifest, candidate = report_inputs

    def changed_during_evaluation(*args, **kwargs):
        if changed_input == "code":
            monkeypatch.setattr(
                ac_retrieval, "_implementation_hashes", lambda root: {"code": "new"}
            )
        else:
            path = {"query": query, "canonical": candidate, "manifest": manifest}[changed_input]
            path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        return {"queries": [], "summary": {}}

    monkeypatch.setattr(ac_retrieval, "evaluate_queries", changed_during_evaluation)
    with pytest.raises(ValueError, match="changed"):
        build_report(data_root, query, case_text_view="canonical")


def test_cli_defaults_to_facts_and_never_writes_to_canonical(tmp_path, monkeypatch, capsys):
    from casepath.evaluation import ac_retrieval

    seen = []

    def built(*args, **kwargs):
        seen.append(kwargs["case_text_view"])
        return {"mode": "test", "queries": [], "summary": {}}

    monkeypatch.setattr(ac_retrieval, "build_report", built)
    assert (
        ac_retrieval.main(
            ["--data-root", str(tmp_path), "--output", str(tmp_path / "canonical/rules.json")]
        )
        == 1
    )
    assert seen == ["facts"]
    assert "no successful report written" in capsys.readouterr().err
    assert not (tmp_path / "canonical/rules.json").exists()


def test_official_queries_require_manifest_but_custom_inputs_can_be_unpinned(tmp_path):
    with pytest.raises(ValueError, match="requires its pinned manifest"):
        _benchmark_manifest(tmp_path / "ac_queries.jsonl", tmp_path, 1)
    assert _benchmark_manifest(tmp_path / "custom_queries.jsonl", tmp_path, 1) is None


def test_custom_queries_do_not_accidentally_use_neighboring_official_manifest(report_inputs):
    data_root, query, _, _ = report_inputs
    custom = query.with_name("custom.jsonl")
    _write(custom, [_row()])
    assert _benchmark_manifest(custom, data_root, 1) is None


@pytest.mark.parametrize(
    "value",
    [
        [],
        {},
        {"files": []},
        {"files": {"ac_queries.jsonl": {}}},
        {"files": {"ac_queries.jsonl": {"sha256": 1, "records": True}}},
    ],
)
def test_malformed_manifest_is_a_validation_error_not_keyerror(report_inputs, value):
    data_root, query, manifest, _ = report_inputs
    manifest.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        _benchmark_manifest(query, data_root, 1)


@pytest.mark.parametrize("kind", ["json", "jsonl"])
def test_annotation_inputs_are_pinned_by_hash_and_record_count(report_inputs, kind):
    data_root, query, manifest, _ = report_inputs
    annotation = data_root / f"annotations.{kind}"
    annotation.write_text(
        '[{"a":1}]\n' if kind == "json" else '{"a":1}\n', encoding="utf-8", newline="\n"
    )
    item = json.loads(manifest.read_text(encoding="utf-8"))
    item["annotation_sources"] = [
        {
            "path": annotation.name,
            "sha256": hashlib.sha256(annotation.read_bytes()).hexdigest(),
            "records": 1,
        }
    ]
    manifest.write_text(json.dumps(item), encoding="utf-8")
    assert _benchmark_manifest(query, data_root, 1) is not None
    item["annotation_sources"][0]["records"] = 2
    manifest.write_text(json.dumps(item), encoding="utf-8")
    with pytest.raises(ValueError, match="count changed"):
        _benchmark_manifest(query, data_root, 1)
    item["annotation_sources"][0]["records"] = 1
    manifest.write_text(json.dumps(item), encoding="utf-8")
    annotation.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot changed"):
        _benchmark_manifest(query, data_root, 1)


@pytest.mark.parametrize("redirect", ["canonical", "outside"])
def test_writer_rejects_reports_root_redirect_even_if_target_is_new(
    tmp_path, monkeypatch, redirect
):
    # Emulate a resolved junction so this guard is tested even without Windows symlink privilege.
    data_root = tmp_path / "data"
    reports = data_root / "reports"
    target = data_root / "canonical" if redirect == "canonical" else tmp_path / "outside"
    original = Path.resolve

    def resolved(path, *args, **kwargs):
        if path == reports:
            return original(target)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolved)
    with pytest.raises(ValueError, match="symlink or junction"):
        write_report(reports / "new.json", {}, data_root=data_root)
    assert not target.exists()


def test_runtime_spies_never_receive_gold_fields_and_no_network_is_used(catalog, monkeypatch):
    import socket

    from casepath.evaluation import ac_retrieval

    original_a = ac_retrieval.HybridCypherRuleRetriever.retrieve
    original_c = ac_retrieval.HybridCypherCaseRetriever.retrieve
    calls = []

    def a_spy(self, state):
        calls.append(("A", state.model_dump(mode="json")))
        assert not state.candidate_claims and not state.condition_states and not state.user_facts
        return original_a(self, state)

    def c_spy(self, state, refs):
        calls.append(("C", state.model_dump(mode="json")))
        assert refs
        return original_c(self, state, refs)

    def forbidden_network(*args, **kwargs):
        pytest.fail("offline evaluator attempted a network call")

    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    monkeypatch.setattr(ac_retrieval.HybridCypherRuleRetriever, "retrieve", a_spy)
    monkeypatch.setattr(ac_retrieval.HybridCypherCaseRetriever, "retrieve", c_spy)
    query = replace(
        _query("C"),
        judgment_basis="never-inject-this-basis",
        source_refs=("never-inject-this-source",),
    )
    evaluate_queries(catalog, [query])
    assert [name for name, _ in calls] == ["A", "C"]
    serialized = json.dumps(calls, ensure_ascii=False)
    assert "never-inject-this" not in serialized
    assert "qrels" not in serialized
