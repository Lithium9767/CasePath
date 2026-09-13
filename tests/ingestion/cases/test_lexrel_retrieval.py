import copy
import hashlib
import json
import urllib.request

import pytest

from casepath.contracts import CaseIndexRecord
from casepath.ingestion.cases import lexrel_retrieval as retrieval


def _sample(number, facts, names):
    return {
        "uniqid": f"00000000-0000-4000-8000-{number:012d}",
        "input": facts,
        "relation": {
            name: [{"主体": "ANNOTATED_PARTIES", "客体": None, "内容": "ANNOTATED_RIGHTS"}]
            for name in names
        },
    }


@pytest.fixture
def snapshot():
    return [
        _sample(1, " 1. 甲向乙借款100元。\n", [" 借款合同关系", "保证合同关系"]),
        _sample(2, "1. 丙向丁借款200元。", ["借款合同关系"]),
        _sample(3, "1. 戊担任借款保证人。", ["保证合同关系"]),
        _sample(4, "1. 己购买一辆自行车。", ["独有关系"]),
        _sample(5, "1. 庚提供了另一项事实。", []),
        _sample(6, "1. 辛提出了补充事实。", [""]),
    ]


def _pin(monkeypatch, samples):
    content = json.dumps(samples, ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(retrieval, "EXPECTED_TEST_SHA256", hashlib.sha256(content).hexdigest())
    monkeypatch.setattr(retrieval, "EXPECTED_TEST_BYTES", len(content))
    monkeypatch.setattr(retrieval, "EXPECTED_TEST_COUNT", len(samples))
    return content


def test_fact_corpus_has_no_annotation_enrichment_and_preserves_original_input(
    monkeypatch, snapshot
):
    build = retrieval.build_snapshot(_pin(monkeypatch, snapshot))
    assert len(build.cases) == 6
    assert build.cases[0].facts_text == snapshot[0]["input"]
    for case in build.cases:
        assert case.title == f"LexRel {case.case_id.removeprefix('lexrel.')}"
        assert case.case_no is None and case.court is None and case.judgment_date is None
        assert case.cause is None
        assert case.keywords == case.related_provision_ids == []
        assert case.reasoning_text == case.summary == ""
        assert case.maturity == "L1"
    corpus = build.files()["cases_l1.jsonl"].decode("utf-8")
    assert "ANNOTATED_PARTIES" not in corpus and "ANNOTATED_RIGHTS" not in corpus
    assert "借款合同关系" not in corpus and "保证合同关系" not in corpus
    assert "claims" not in corpus and "decisions" not in corpus
    for line in corpus.splitlines():
        CaseIndexRecord.model_validate_json(line)


def test_qrels_share_any_relation_exclude_self_and_skip_unlabeled_or_singleton(
    monkeypatch, snapshot
):
    build = retrieval.build_snapshot(_pin(monkeypatch, snapshot))
    first, second, third, singleton, empty, blank = build.qrels
    assert first["relevant_case_ids"] == [second["query_case_id"], third["query_case_id"]]
    assert second["relevant_case_ids"] == third["relevant_case_ids"] == [first["query_case_id"]]
    assert singleton["skip_reason"] == "no_shared_relation_peer"
    assert empty["skip_reason"] == blank["skip_reason"] == "no_valid_relation_label"
    for row in build.qrels:
        assert row["query_case_id"] not in row["relevant_case_ids"]
        assert row["exclude_case_ids"] == [row["query_case_id"]]
    assert [row["evaluable"] for row in build.qrels] == [True, True, True, False, False, False]
    assert build.manifest["counts"]["directed_positive_pairs"] == 4
    assert build.manifest["counts"]["unlabeled_distractors"] == 2
    assert build.manifest["counts"]["labeled_without_peer"] == 1
    assert build.labels[0]["raw_relation_types"][0] == " 借款合同关系"
    assert build.labels[0]["relation_types"] == ["保证合同关系", "借款合同关系"]


def test_wording_variants_are_not_silently_merged(monkeypatch, snapshot):
    snapshot[1]["relation"] = {"借款合同法律关系": [{"主体": "", "客体": "", "内容": ""}]}
    build = retrieval.build_snapshot(_pin(monkeypatch, snapshot))
    assert build.qrels[1]["evaluable"] is False


def test_hash_mismatch_rejected_before_parsing():
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        retrieval.build_snapshot(b"not even JSON")


@pytest.mark.parametrize("problem", ["id", "text", "schema", "relation", "argument", "empty"])
def test_invalid_snapshot_rejected_even_when_hash_matches(monkeypatch, snapshot, problem):
    if problem == "id":
        snapshot[1]["uniqid"] = snapshot[0]["uniqid"]
    elif problem == "text":
        snapshot[1]["input"] = snapshot[0]["input"].replace(" ", "").replace("\n", "")
    elif problem == "schema":
        snapshot[0]["court"] = "Fabricated court"
    elif problem == "relation":
        snapshot[0]["relation"] = []
    elif problem == "argument":
        snapshot[0]["relation"][" 借款合同关系"][0]["内容"] = 12
    else:
        snapshot[0]["input"] = " \n "
    with pytest.raises((TypeError, ValueError)):
        retrieval.build_snapshot(_pin(monkeypatch, snapshot))


def test_input_order_does_not_change_derived_records(monkeypatch, snapshot):
    build = retrieval.build_snapshot(_pin(monkeypatch, snapshot))
    shuffled = list(reversed(copy.deepcopy(snapshot)))
    other = retrieval.build_snapshot(_pin(monkeypatch, shuffled))
    assert build.cases == other.cases
    assert build.labels == other.labels
    assert build.qrels == other.qrels


def test_write_is_idempotent_and_refuses_foreign_or_changed_files(monkeypatch, snapshot, tmp_path):
    build = retrieval.build_snapshot(_pin(monkeypatch, snapshot))
    output = tmp_path / "generated"
    manifest_path = retrieval.write_build(build, output)
    assert manifest_path == output / "manifest.json"
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    retrieval.write_build(build, output)
    assert before == {path.name: path.read_bytes() for path in output.iterdir()}
    (output / "cases_l1.jsonl").write_text("user changes", encoding="utf-8")
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        retrieval.write_build(build, output)
    assert (output / "cases_l1.jsonl").read_text(encoding="utf-8") == "user changes"
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "notes.txt").write_text("user file", encoding="utf-8")
    with pytest.raises(ValueError, match="Refusing existing output"):
        retrieval.write_build(build, foreign)
    assert (foreign / "notes.txt").read_text(encoding="utf-8") == "user file"


def test_protected_output_and_input_overlap_are_rejected(tmp_path):
    for output in (
        retrieval.REPOSITORY_ROOT,
        retrieval.REPOSITORY_ROOT / "data",
        retrieval.REPOSITORY_ROOT / "data/canonical/cases",
        retrieval.REPOSITORY_ROOT / "data/inputs/subdir",
    ):
        with pytest.raises(ValueError, match="overlap"):
            retrieval.validate_output_path(output)
    with pytest.raises(ValueError, match="input path"):
        retrieval.validate_output_path(tmp_path, tmp_path / "test.json")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/LexRel/test.json",
        "http://raw.githubusercontent.com/LexRel/test.json",
        "https://raw.githubusercontent.com.evil.example/LexRel/test.json",
        "https://user@raw.githubusercontent.com/LexRel/test.json",
        "https://raw.githubusercontent.com:8443/LexRel/test.json",
    ],
)
def test_download_redirect_rejects_nonofficial_hosts_and_downgrades(url):
    handler = retrieval._OfficialRedirects()
    with pytest.raises(ValueError, match="official HTTPS raw host"):
        handler.redirect_request(urllib.request.Request(retrieval.TEST_URL), None, 302, "", {}, url)


def test_download_checks_final_url_and_limits_response_size(monkeypatch):
    class Response:
        url = retrieval.TEST_URL
        body = b"pinned"
        read_limit = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def geturl(self):
            return self.url

        def read(self, limit):
            self.read_limit = limit
            return self.body[:limit]

    response = Response()

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == retrieval.TEST_URL
            assert timeout == 60
            return response

    monkeypatch.setattr(urllib.request, "build_opener", lambda handler: Opener())
    monkeypatch.setattr(retrieval, "EXPECTED_TEST_BYTES", len(response.body))
    assert retrieval.download_snapshot() == b"pinned"
    assert response.read_limit == len(b"pinned") + 1
    response.body = b"oversized"
    with pytest.raises(ValueError, match="exceeds"):
        retrieval.download_snapshot()
    response.url = "https://example.org/stolen.json"
    with pytest.raises(ValueError, match="official HTTPS raw host"):
        retrieval.download_snapshot()


def test_check_only_local_input_writes_nothing_and_does_not_access_network(
    monkeypatch, snapshot, tmp_path, capsys
):
    content = _pin(monkeypatch, snapshot)
    source = tmp_path / "test.json"
    source.write_bytes(content)
    output = tmp_path / "not-created" / "dataset"
    monkeypatch.setattr(retrieval, "download_snapshot", lambda: pytest.fail("unexpected network"))
    assert retrieval.main(["--input", str(source), "--output", str(output), "--check-only"]) == 0
    assert not output.parent.exists()
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["counts"]["cases"] == 6
    assert "事实" not in json.dumps(receipt, ensure_ascii=False)


def test_invalid_input_does_not_create_output(monkeypatch, tmp_path, capsys):
    source = tmp_path / "test.json"
    source.write_bytes(b"[]")
    output = tmp_path / "missing" / "dataset"
    with pytest.raises(SystemExit) as error:
        retrieval.main(["--input", str(source), "--output", str(output)])
    assert error.value.code == 2
    assert not output.parent.exists()
    assert "SHA-256 mismatch" in capsys.readouterr().err
