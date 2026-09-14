import json

import pytest

from casepath.evaluation import condition_benchmark as benchmark
from casepath.evaluation.condition_benchmark import (
    _artifact,
    _queries,
    load_judgments,
    load_scenarios,
)


def _write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _scenario():
    return {
        "query_id": "C2.01", "query_text": "A natural contract question", "domain": "contract",
        "comparison_focus": ["performance", "notice"], "rubric_notes": "compare the facts",
        "source_case_ids": ["case.1"],
    }


def _judgment(grade=2):
    return {
        "query_id": "C2.01", "case_id": "case.1", "grade": grade,
        "rationale": "The issue is shared, but notice and completed performance differ.",
        "evidence": [{"field": "facts_text", "quote": "performance was incomplete"}],
    }


def test_scenario_provenance_and_review_focus_never_enter_runtime(tmp_path):
    source = _scenario()
    rows = load_scenarios([_write(tmp_path / "q.jsonl", [source])], {"case.1"})
    query = _queries(rows)[0]
    assert query.query_text == source["query_text"]
    assert query.qrels == {} and query.source_refs == () and query.exclude_ids == ()
    assert not hasattr(query, "comparison_focus")


@pytest.mark.parametrize("change", [
    {"source_case_ids": ["unknown"]}, {"comparison_focus": []},
    {"source_case_ids": ["case.1", "case.1"]}, {"query_id": "A.1"},
    {"query_text": " "}, {"qrels": {"case.1": 3}},
])
def test_scenarios_reject_invalid_inputs(tmp_path, change):
    with pytest.raises(ValueError):
        load_scenarios([_write(tmp_path / "q.jsonl", [{**_scenario(), **change}])], {"case.1"})


@pytest.mark.parametrize("grade", [0, 1, 2, 3, None])
def test_all_grades_need_source_support_and_null_is_preserved(tmp_path, grade):
    pool = [{"query_id": "C2.01", "case_ids": ["case.1"]}]
    cases = {"case.1": {"facts_text": "The performance was incomplete."}}
    result = load_judgments([_write(tmp_path / "j.jsonl", [_judgment(grade)])], pool, cases)
    assert result[("C2.01", "case.1")]["grade"] == grade


@pytest.mark.parametrize("change", [
    {"grade": True}, {"grade": 4}, {"grade": -1}, {"grade": "3"},
    {"case_id": "unknown"}, {"query_id": "C2.99"}, {"rationale": "similar"},
    {"evidence": []}, {"evidence": [{"field": "title", "quote": "contract"}]},
    {"evidence": [{"field": "facts_text", "quote": "invented facts"}]},
])
def test_judgments_reject_unsupported_or_unknown_labels(tmp_path, change):
    pool = [{"query_id": "C2.01", "case_ids": ["case.1"]}]
    cases = {"case.1": {"facts_text": "The performance was incomplete."}}
    with pytest.raises(ValueError):
        load_judgments([_write(tmp_path / "j.jsonl", [{**_judgment(), **change}])], pool, cases)


def test_missing_or_duplicate_pair_never_silently_scores(tmp_path):
    cases = {"case.1": {"facts_text": "The performance was incomplete."},
             "case.2": {"facts_text": "A different case."}}
    path = _write(tmp_path / "j.jsonl", [_judgment()])
    with pytest.raises(ValueError, match="missing"):
        load_judgments([path], [{"query_id": "C2.01", "case_ids": ["case.1", "case.2"]}], cases)
    with pytest.raises(ValueError, match="duplicate"):
        load_judgments([path, path], [{"query_id": "C2.01", "case_ids": ["case.1"]}], cases)


def test_artifacts_are_create_only_or_byte_identical(tmp_path):
    path = tmp_path / "frozen.json"
    _artifact(path, {"value": 1})
    _artifact(path, {"value": 1})
    with pytest.raises(ValueError, match="overwrite"):
        _artifact(path, {"value": 2})


def _score_fixture(tmp_path, monkeypatch, *, grade=2, ranked=None):
    root = tmp_path / "data"
    source_rel = "canonical/cases/cases_l1.jsonl"
    source = root / source_rel
    source.parent.mkdir(parents=True)
    cases = [{"case_id": f"case.{i}", "facts_text": "The performance was incomplete."}
             for i in (1, 2, 3)]
    _write(source, cases)
    monkeypatch.setattr(benchmark, "_CANONICAL", (source_rel,))
    query_path, pool_path = root / "evaluation/c2_queries.jsonl", root / "evaluation/c2_pool.jsonl"
    _artifact(query_path, [_scenario()], jsonl=True)
    _artifact(pool_path, [{"query_id": "C2.01", "case_ids": ["case.1", "case.2"]}], jsonl=True)
    runs = {
        "implementation_sha256": {"synthetic.py": "frozen-test-implementation"},
        "depth": 2, "queries_sha256": benchmark._sha(query_path),
        "pool_sha256": benchmark._sha(pool_path),
        "canonical_sha256": {source_rel: benchmark._sha(source)},
        "candidate_ids": [r["case_id"] for r in cases], "scope": {"scope": "contract"},
        "results": {"facts": {"C2.01": {"rankings": {
            s: ["case.1", "case.2"] if ranked is None else ranked for s in benchmark.PRIMARY
        }}}},
    }
    _artifact(root / "reports/c2_pool.runs.json", runs)
    path = root / "evaluation/judgments.jsonl"
    _write(path, [_judgment(3), {**_judgment(grade), "case_id": "case.2"}])
    return root, path


@pytest.mark.parametrize("prefix", ["../c2", "c2/other", "c2\\other", "c2..other", "other", ""])
def test_version_prefix_rejects_unsafe_or_ambiguous_paths(prefix):
    with pytest.raises(ValueError, match="prefix"):
        benchmark._prefix(prefix)


def test_baseline_rescoring_uses_same_pool_and_preserves_frozen_rankings(tmp_path, monkeypatch):
    root, path = _score_fixture(tmp_path, monkeypatch)
    original = root / "reports/c2_pool.runs.json"
    before = original.read_bytes()
    data = json.loads(before)
    for stage in benchmark.PRIMARY:
        data["results"]["facts"]["C2.01"]["rankings"][stage] = ["case.2", "case.1"]
    baseline = root / "reports/earlier.runs.json"
    _artifact(baseline, data)
    before_baseline = baseline.read_bytes()
    report = benchmark.score(root, [path], k=[1], baseline_run=baseline)
    row = report["queries"][0]
    assert row["rankings"]["c.fused"]["ranked_ids"] == ["case.2", "case.1"]
    assert report["comparison_role"] == "baseline_rescored_on_same_pool"
    assert report["ranking_run_sha256"] == benchmark._sha(baseline)
    assert report["pool_run_sha256"] == benchmark._sha(original)
    assert before == original.read_bytes() and before_baseline == baseline.read_bytes()


@pytest.mark.parametrize("field,value", [
    ("queries_sha256", "different"), ("canonical_sha256", {}),
    ("candidate_ids", ["case.1"]), ("results", {"facts": {}}),
])
def test_paired_baseline_rejects_changed_problem_or_candidate_universe(
    tmp_path, monkeypatch, field, value,
):
    root, path = _score_fixture(tmp_path, monkeypatch)
    baseline = root / "reports/bad.runs.json"
    data = json.loads((root / "reports/c2_pool.runs.json").read_text(encoding="utf-8"))
    data[field] = value
    _artifact(baseline, data)
    with pytest.raises(ValueError, match="baseline"):
        benchmark.score(root, [path], k=[1], baseline_run=baseline)


def test_null_outside_top_k_suppresses_whole_query_estimate(tmp_path, monkeypatch):
    root, path = _score_fixture(tmp_path, monkeypatch, grade=None)
    report = benchmark.score(root, [path], k=[1])
    for stage in benchmark.PRIMARY:
        row = report["queries"][0]["rankings"][stage]
        assert row["ranked_ids"] == ["case.1", "case.2"]
        assert row["metrics"]["status"] == "incomplete_judgments"
        assert row["metrics"]["at_k"]["1"]["recall"] is None
        assert row["metrics"]["at_k"]["1"]["ndcg"] is None
        assert report["summary"][stage]["incomplete_query_count"] == 1
        assert report["summary"][stage]["no_positive_query_count"] == 0


def test_known_but_unpooled_top_k_is_rejected(tmp_path, monkeypatch):
    root, path = _score_fixture(tmp_path, monkeypatch, ranked=["case.3"])
    with pytest.raises(ValueError, match="outside the reviewed pool"):
        benchmark.score(root, [path], k=[1])


def test_empty_rank_is_zero_recall_but_undefined_judged_coverage(tmp_path, monkeypatch):
    root, path = _score_fixture(tmp_path, monkeypatch, ranked=[])
    report = benchmark.score(root, [path], k=[1])
    for summary in report["summary"].values():
        assert summary["macro_at_k"]["1"]["recall"] == 0
        assert summary["macro_judged_fraction"]["1"] is None
        assert summary["empty_ranking_query_count"] == 1


def test_no_scoring_beyond_review_depth(tmp_path, monkeypatch):
    root, path = _score_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="exceeds"):
        benchmark.score(root, [path], k=[3])


def test_grade_change_during_scoring_is_rejected(tmp_path, monkeypatch):
    root, path = _score_fixture(tmp_path, monkeypatch)
    original = benchmark._read_jsonl

    def read_and_modify(target):
        result = original(target)
        if target == path:
            path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        return result

    monkeypatch.setattr(benchmark, "_read_jsonl", read_and_modify)
    with pytest.raises(ValueError, match="changed during scoring"):
        benchmark.score(root, [path], k=[1])


def test_duplicate_judgment_paths_are_rejected(tmp_path, monkeypatch):
    root, path = _score_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="duplicate judgment input"):
        benchmark.score(root, [path, path], k=[1])


def test_background_is_not_counted_as_comparison_positive(tmp_path, monkeypatch):
    root, path = _score_fixture(tmp_path, monkeypatch, grade=1)
    report = benchmark.score(root, [path], k=[1])
    for summary in report["summary"].values():
        assert summary["macro_at_k"]["1"]["recall"] == 0.5
        assert summary["macro_at_k"]["1"]["comparison_recall"] == 1.0
