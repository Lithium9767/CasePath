import json
from pathlib import Path

from casepath.ingestion.cases.cleaner import CaseCleaner, split_sections


def test_split_sections_preserves_headings():
    sections = split_sections("# x\n\n## 基本案情\n事实\n## 裁判理由\n理由")
    assert sections["基本案情"] == "事实"
    assert sections["裁判理由"] == "理由"


def test_filters_only_civil_and_writes_jsonl(tmp_path: Path):
    src = tmp_path / "cases.json"
    src.write_text(json.dumps([
        {"case_id": "1", "title": "民事案", "case_category": "民事", "case_cause": "合同", "case_facts": "事实"},
        {"case_id": "2", "title": "刑事案", "case_category": "刑事"},
    ], ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "cases.jsonl"
    assert CaseCleaner(src).write(out) == 1
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert rows[0]["case_category"] == "民事"


def test_deep_record_uses_unknown_when_result_missing():
    cleaner = CaseCleaner(Path("/tmp/nonexistent"))
    record = cleaner.deep_fitness({"case_id": "x", "title": "健身案", "case_cause": "服务合同纠纷", "full_document": "## 基本案情\n事实\n## 裁判理由\n理由"})
    assert record.decisions[0].status == "UNKNOWN"
    assert record.source_spans[0].content_hash
