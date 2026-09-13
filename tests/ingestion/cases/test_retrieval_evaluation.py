from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from casepath.ingestion.cases import lexrel_retrieval as builder
from casepath.ingestion.cases import retrieval_evaluation as evaluation


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


@pytest.fixture
def fixture_corpus(monkeypatch, tmp_path):
    samples = [
        (1, "PRIVATE_FACT_ONE 甲借款100元。", ["借款合同关系"]),
        (2, "PRIVATE_FACT_TWO 乙借款200元。", [" 借款合同关系"]),
        (3, "PRIVATE_FACT_THREE 丙提供担保。", ["保证合同关系"]),
        (4, "PRIVATE_FACT_FOUR 丁购买商品。", []),
    ]
    payload = [
        {
            "uniqid": f"00000000-0000-4000-8000-{number:012d}",
            "input": facts,
            "relation": {
                name: [{"主体": "PRIVATE_ANNOTATION", "客体": None, "内容": "ANNOTATED_RIGHTS"}]
                for name in names
            },
        }
        for number, facts, names in samples
    ]
    content = _json_bytes(payload)
    monkeypatch.setattr(builder, "EXPECTED_TEST_SHA256", hashlib.sha256(content).hexdigest())
    monkeypatch.setattr(builder, "EXPECTED_TEST_BYTES", len(content))
    monkeypatch.setattr(builder, "EXPECTED_TEST_COUNT", len(payload))
    build = builder.build_snapshot(content)
    corpus = tmp_path / "data/derived/lexrel_retrieval"
    corpus.mkdir(parents=True)
    for name, raw in build.files().items():
        (corpus / name).write_bytes(raw)
    receipt = tmp_path / "data/reports/lexrel_retrieval.manifest.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_bytes((corpus / "manifest.json").read_bytes())
    monkeypatch.setattr(evaluation, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(evaluation, "TRUSTED_RECEIPT", receipt)
    monkeypatch.setattr(evaluation, "QUERY_SPECS", evaluation.QUERY_SPECS[:2])
    return corpus, receipt


def _mutate_signed_file(fixture_corpus, name, modify):
    """Exercise semantic guards even with a deliberately re-signed bad fixture."""
    corpus, receipt = fixture_corpus
    path = corpus / name
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    modify(rows)
    content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")
    path.write_bytes(content)
    manifest = json.loads((corpus / "manifest.json").read_bytes())
    manifest["files"][name] = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    (corpus / "manifest.json").write_bytes(_json_bytes(manifest))
    receipt.write_bytes(_json_bytes(manifest))


def _predictions(path: Path, records):
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records), encoding="utf-8"
    )
    return path


def test_load_validates_receipt_and_all_rows(fixture_corpus):
    corpus = evaluation.load_corpus(fixture_corpus[0])
    assert len(corpus.facts) == len(corpus.labels) == len(corpus.qrels) == 4
    assert sum(bool(positive) for positive in corpus.qrels.values()) == 2
    assert set(corpus.hashes) == {
        "cases_l1.jsonl",
        "labels.jsonl",
        "qrels.jsonl",
        "manifest.json",
        "trusted_receipt",
    }


def test_only_facts_enter_index_and_report_does_not_disclose_text(monkeypatch, fixture_corpus):
    corpus = evaluation.load_corpus(fixture_corpus[0])
    indexed = []

    class SpyIndex:
        def __init__(self, documents):
            indexed.append(documents)

        def search(self, query, top_k):
            return [(case_id, 1.0) for case_id in sorted(corpus.facts)][:top_k]

    monkeypatch.setattr(evaluation, "BM25Index", SpyIndex)
    report = evaluation.evaluate(corpus, k=[1, 2])
    assert indexed == [corpus.facts]
    assert report["counts"] == {
        "candidates": 4,
        "queries": 2,
        "evaluated": 2,
        "skipped_no_positive": 0,
    }
    text = json.dumps(report, ensure_ascii=False)
    assert "PRIVATE_FACT" not in text
    assert "PRIVATE_ANNOTATION" not in text and "ANNOTATED_RIGHTS" not in text
    assert all(
        "query_text" not in row and "ranked_case_ids" not in row for row in report["queries"]
    )
    assert report["queries"][0]["positive_count"] == 2


def test_case_overlap_removes_self_before_cutoff_and_skips_no_positive(monkeypatch, fixture_corpus):
    corpus = evaluation.load_corpus(fixture_corpus[0])
    requested = []

    class SelfFirstIndex:
        def __init__(self, documents):
            assert documents == corpus.facts

        def search(self, query, top_k):
            requested.append(top_k)
            self_id = next(case_id for case_id, facts in corpus.facts.items() if facts == query)
            peer = next(iter(corpus.qrels[self_id]))
            return [(self_id, 2.0), (peer, 1.0)][:top_k]

    monkeypatch.setattr(evaluation, "BM25Index", SelfFirstIndex)
    report = evaluation.evaluate(corpus, protocol="case_overlap", k=[1])
    assert requested == [2, 2]
    assert report["counts"]["evaluated"] == 2
    assert report["counts"]["skipped_no_positive"] == 2
    assert report["metrics"] == {"macro_recall@1": 1.0}
    assert [row["skip_reason"] for row in report["queries"][-2:]] == [
        "no_shared_relation_peer",
        "no_valid_relation_label",
    ]


@pytest.mark.parametrize("values", [[], [0], [-1], [True], [1.5], [1, 1]])
def test_invalid_cutoffs_rejected(fixture_corpus, values):
    corpus = evaluation.load_corpus(fixture_corpus[0])
    with pytest.raises(ValueError, match="k"):
        evaluation.evaluate(corpus, k=values)


@pytest.mark.parametrize("name", ["cases_l1.jsonl", "labels.jsonl", "qrels.jsonl"])
def test_damaged_file_hash_rejected(fixture_corpus, name):
    path = fixture_corpus[0] / name
    content = path.read_bytes()
    path.write_bytes(content[:-1] + b" ")
    with pytest.raises(ValueError, match="SHA-256"):
        evaluation.load_corpus(fixture_corpus[0])


def test_local_manifest_cannot_reauthorize_changed_data(fixture_corpus):
    path = fixture_corpus[0] / "manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["counts"]["cases"] += 1
    path.write_bytes(_json_bytes(manifest))
    with pytest.raises(ValueError, match="trusted receipt"):
        evaluation.load_corpus(fixture_corpus[0])


@pytest.mark.parametrize(
    ("name", "mutation", "message"),
    [
        ("cases_l1.jsonl", lambda rows: rows.append(rows[0]), "duplicate case ID"),
        (
            "cases_l1.jsonl",
            lambda rows: rows[0].update(case_id="lexrel.not-a-uuid"),
            "canonical case UUID",
        ),
        ("cases_l1.jsonl", lambda rows: rows[0].update(facts_text=" "), "empty case facts"),
        (
            "cases_l1.jsonl",
            lambda rows: rows[1].update(facts_text=rows[0]["facts_text"]),
            "duplicate normalized",
        ),
        (
            "cases_l1.jsonl",
            lambda rows: rows[0].update(keywords=["借款合同关系"]),
            "annotation-enriched",
        ),
        ("labels.jsonl", lambda rows: rows.append(rows[0]), "duplicate label case ID"),
        ("labels.jsonl", lambda rows: rows.pop(), "complete corpus"),
        ("labels.jsonl", lambda rows: rows[0].update(case_id="unknown"), "unknown case"),
        ("qrels.jsonl", lambda rows: rows.append(rows[0]), "duplicate qrels query ID"),
        (
            "qrels.jsonl",
            lambda rows: rows[0].update(relevant_case_ids=["unknown"]),
            "unknown positive",
        ),
        (
            "qrels.jsonl",
            lambda rows: rows[0].update(relevant_case_ids=[rows[0]["query_case_id"]]),
            "own query",
        ),
        ("qrels.jsonl", lambda rows: rows[0].update(exclude_case_ids=[]), "exclude its own query"),
        ("qrels.jsonl", lambda rows: rows[0].update(relevant_case_ids=[]), "do not match"),
        ("qrels.jsonl", lambda rows: rows[0].update(evaluable=False), "evaluable flag"),
        ("qrels.jsonl", lambda rows: rows.pop(), "complete corpus"),
    ],
)
def test_structural_defects_rejected_even_with_matching_fixture_hashes(
    fixture_corpus, name, mutation, message
):
    _mutate_signed_file(fixture_corpus, name, mutation)
    with pytest.raises(ValueError, match=message) as error:
        evaluation.load_corpus(fixture_corpus[0])
    assert "PRIVATE_FACT" not in str(error.value)


def test_prediction_scoring_does_not_build_index_and_counts_empty_rankings(
    monkeypatch, fixture_corpus, tmp_path
):
    corpus = evaluation.load_corpus(fixture_corpus[0])
    first, second = sorted(case_id for case_id, positive in corpus.qrels.items() if positive)
    path = _predictions(
        tmp_path / "predictions.jsonl",
        [
            {"query_id": first, "ranked_case_ids": [first, second]},
            {"query_id": second, "ranked_case_ids": []},
        ],
    )
    monkeypatch.setattr(
        evaluation, "BM25Index", lambda _: pytest.fail("external evaluation built an index")
    )
    report = evaluation.evaluate(corpus, protocol="case_overlap", k=[1], predictions_path=path)
    assert report["metrics"] == {"macro_recall@1": 0.5}
    assert report["predictions_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report["retriever"] == "external_rankings_not_audited"


@pytest.mark.parametrize(
    "problem",
    ["duplicate_query", "duplicate_rank", "unknown_case", "missing_query", "skipped_query"],
)
def test_invalid_external_predictions_rejected(fixture_corpus, tmp_path, problem):
    corpus = evaluation.load_corpus(fixture_corpus[0])
    first, second, third, _ = sorted(corpus.facts)
    rows = [
        {"query_id": first, "ranked_case_ids": [second]},
        {"query_id": second, "ranked_case_ids": [first]},
    ]
    if problem == "duplicate_query":
        rows.append(rows[0])
    elif problem == "duplicate_rank":
        rows[0]["ranked_case_ids"] = [second, second]
    elif problem == "unknown_case":
        rows[0]["ranked_case_ids"] = ["not-in-corpus"]
    elif problem == "missing_query":
        rows.pop()
    else:
        rows.append({"query_id": third, "ranked_case_ids": []})
    path = _predictions(tmp_path / "predictions.jsonl", rows)
    with pytest.raises(ValueError):
        evaluation.evaluate(corpus, protocol="case_overlap", predictions_path=path)


@pytest.mark.parametrize(
    "relative",
    [
        "data/canonical/report.json",
        "data/inputs/report.json",
        "data/manifests/report.json",
        "data/derived/lexrel_retrieval/report.json",
        "data/reports/lexrel_retrieval.manifest.json",
    ],
)
def test_report_cannot_overwrite_protected_data(fixture_corpus, tmp_path, relative):
    with pytest.raises(ValueError, match="overlap"):
        evaluation.write_report({}, tmp_path / relative, fixture_corpus[0])


def test_report_is_idempotent_and_refuses_unrelated_existing_file(fixture_corpus, tmp_path):
    output = tmp_path / "data/reports/recall.json"
    report = {"generator": evaluation.GENERATOR, "counts": {"queries": 13}}
    evaluation.write_report(report, output, fixture_corpus[0])
    before = output.read_bytes()
    evaluation.write_report(report, output, fixture_corpus[0])
    assert output.read_bytes() == before
    with pytest.raises(ValueError, match="overwrite"):
        evaluation.write_report({"other": "report"}, output, fixture_corpus[0])
    assert output.read_bytes() == before


def test_cli_failure_does_not_echo_sensitive_input_or_write_report(
    fixture_corpus, tmp_path, capsys
):
    _mutate_signed_file(
        fixture_corpus,
        "cases_l1.jsonl",
        lambda rows: rows[0].update(case_id={"PRIVATE_FACT": "SECRET"}),
    )
    output = tmp_path / "missing/report.json"
    with pytest.raises(SystemExit) as error:
        evaluation.main(["--corpus", str(fixture_corpus[0]), "--output", str(output)])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert "PRIVATE_FACT" not in captured.err and "SECRET" not in captured.err
    assert "failed" in captured.err
    assert not output.parent.exists()
