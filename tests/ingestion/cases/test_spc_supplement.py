from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from casepath.contracts import CaseIndexRecord, MaturityLevel
from casepath.ingestion.cases import spc_supplement as spc
from casepath.ingestion.laws.jsonl import read_jsonl, sha256_file

ROOT = Path(__file__).resolve().parents[3]
INPUT = ROOT / spc.INPUT_PATH
PROVENANCE = ROOT / "data/reports/spc_civil_code_first_batch.provenance.json"


def _rows() -> list[dict]:
    return [json.loads(line) for line in INPUT.read_text(encoding="utf-8").splitlines()]


def _rehashed_input(tmp_path: Path, monkeypatch, rows: list[dict]) -> Path:
    path = tmp_path / "spc.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    # Exercise structural defenses independently of the immutable production
    # hash, as if a future release had accidentally rebased that hash.
    monkeypatch.setattr(spc, "INPUT_SHA256", sha256_file(path))
    return path


def test_pinned_summaries_are_six_l1_only_records():
    records = spc.load_spc_cases(INPUT)
    assert sha256_file(INPUT) == spc.INPUT_SHA256
    assert len(records) == spc.COUNT == 6
    assert {record.case_id for record in records} == spc.CASE_IDS
    assert [record.case_id for record in records] == sorted(spc.CASE_IDS)
    for record in records:
        assert isinstance(record, CaseIndexRecord)
        assert record.maturity == MaturityLevel.L1
        assert record.facts_text.strip()
        assert record.summary.startswith(spc.SUMMARY_PREFIX)
        assert record.case_no is record.court is record.judgment_date is None
        assert not {"claims", "findings", "source_spans"} & set(record.model_dump())


def test_new_summaries_do_not_duplicate_existing_canonical_cases():
    existing = read_jsonl(ROOT / "data/canonical/cases/cases_l1.jsonl", CaseIndexRecord)
    existing = [record for record in existing if record.case_id not in spc.CASE_IDS]
    normalize = lambda text: re.sub(r"\s+", "", text)
    assert len(existing) == 54
    records = spc.load_spc_cases(INPUT)
    assert not spc.CASE_IDS & {record.case_id for record in existing}
    assert not {record.title for record in records} & {record.title for record in existing}
    assert not {normalize(record.facts_text) for record in records} & {
        normalize(record.facts_text) for record in existing
    }


def test_published_article_guidance_resolves_to_canonical_provisions():
    article_ids = {
        json.loads(line)["provision_id"]
        for line in (ROOT / "data/canonical/rules/provisions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    }
    assert all(
        set(record.related_provision_ids) <= article_ids for record in spc.load_spc_cases(INPUT)
    )


def test_source_check_corrections_remain_explicit():
    records = {record.case_id.rsplit(".", 1)[-1]: record for record in spc.load_spc_cases(INPUT)}
    assert "伸手接触其背部" in records["huang-property-health"].facts_text
    assert "停止侵害" not in records["lou-portrait"].summary
    assert "未生育或收养子女" in records["su-statutory-inheritance"].facts_text
    badminton = records["song-v-zhou-body-right"]
    assert badminton.judgment_date is None
    assert "一审法院于2021年1月4日" in badminton.summary
    assert "未披露二审裁判日期" in badminton.summary
    assert records["yu-high-rise-object"].judgment_date is None


def test_provenance_matches_input_and_keeps_summary_not_verbatim_status():
    provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    assert provenance["input_revision"] == spc.INPUT_REVISION
    assert provenance["input"] == {
        "path": spc.INPUT_PATH,
        "sha256_lf": spc.INPUT_SHA256,
        "record_count": spc.COUNT,
    }
    assert provenance["upstream_editorial_baseline"]["revision"] == "6be749d"
    assert provenance["authority"]["checked_on"] == spc.CHECKED_ON
    representation = provenance["representation"]
    assert representation["text_kind"] == "editorial_summary"
    assert representation["verbatim_source_spans_created"] is False
    assert representation["creates_deep_records"] is False
    assert representation["human_verified"] is False
    assert {row["case_id"] for row in provenance["cases"]} == spc.CASE_IDS
    by_id = {record.case_id: record for record in spc.load_spc_cases(INPUT)}
    for row in provenance["cases"]:
        assert row["source_url"] == spc.SOURCE_URL
        assert row["upstream_p3_revision"] == "6be749d"
        assert row["checked_on"] == spc.CHECKED_ON
        assert row["text_kind"] == "editorial_summary"
        assert row["related_provision_ids"] == by_id[row["case_id"]].related_provision_ids
        assert row["revision_reasons"]
        assert row["index_judgment_date"] is None


def test_windows_newlines_preserve_pinned_hash(tmp_path):
    path = tmp_path / "spc-crlf.jsonl"
    path.write_bytes(INPUT.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert spc.load_spc_cases(path) == spc.load_spc_cases(INPUT)


def test_tampered_summary_fails_before_conversion(tmp_path):
    path = tmp_path / "tampered.jsonl"
    path.write_bytes(INPUT.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="SHA256"):
        spc.load_spc_cases(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("case_id", "case.unverified"),
        ("case_facts_text", "  "),
        ("title", ""),
        ("judgment_reasoning_text", 1),
        ("judgment_summary", None),
        ("source_file", "https://example.com/not-the-source"),
        ("source_section", 6),
        ("source_section", True),
        ("text_kind", "verbatim_judgment"),
        ("case_category", "刑事"),
        ("case_cause", "运输合同纠纷"),
        ("case_no", "unverified"),
        ("court", "unverified"),
        ("judgment_date", "2021-01-04"),
        ("keywords", ["duplicate", "duplicate"]),
        ("keywords", ["", 1]),
        ("related_provision_ids", ["law.prc.civil_code.2021.article_0287"]),
    ],
)
def test_structure_guards_reject_rebased_invalid_input(tmp_path, monkeypatch, field, value):
    rows = _rows()
    rows[0][field] = value
    with pytest.raises(ValueError):
        spc.load_spc_cases(_rehashed_input(tmp_path, monkeypatch, rows))


@pytest.mark.parametrize("mutation", ["duplicate_id", "duplicate_facts", "extra_field", "count"])
def test_no_duplicate_or_unexpected_records(tmp_path, monkeypatch, mutation):
    rows = copy.deepcopy(_rows())
    if mutation == "duplicate_id":
        rows[1]["case_id"] = rows[0]["case_id"]
    elif mutation == "duplicate_facts":
        rows[1]["case_facts_text"] = rows[0]["case_facts_text"].replace("，", "， \n")
    elif mutation == "extra_field":
        rows[0]["maturity"] = "L3"
    else:
        rows.pop()
    with pytest.raises(ValueError):
        spc.load_spc_cases(_rehashed_input(tmp_path, monkeypatch, rows))


def test_duplicate_json_keys_are_rejected(tmp_path, monkeypatch):
    path = tmp_path / "duplicate-key.jsonl"
    text = INPUT.read_text(encoding="utf-8")
    path.write_text(
        text.replace('{"case_no":null,', '{"case_no":null,"case_no":null,', 1), encoding="utf-8"
    )
    monkeypatch.setattr(spc, "INPUT_SHA256", sha256_file(path))
    with pytest.raises(ValueError, match="duplicate JSON key"):
        spc.load_spc_cases(path)
