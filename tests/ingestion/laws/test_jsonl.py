from __future__ import annotations

from pathlib import Path

from casepath.ingestion.laws.civil_code import load_civil_code
from casepath.ingestion.laws.jsonl import (
    sha256_file,
    sha256_text,
    write_json,
    write_jsonl,
)


def test_text_file_hash_is_independent_of_checkout_line_endings(tmp_path: Path) -> None:
    lf_path = tmp_path / "lf.json"
    crlf_path = tmp_path / "crlf.json"
    lone_cr_path = tmp_path / "cr.json"
    content = b'{\n  "value": 1\n}\n'

    lf_path.write_bytes(content)
    crlf_path.write_bytes(content.replace(b"\n", b"\r\n"))
    lone_cr_path.write_bytes(content.replace(b"\n", b"\r"))

    expected = sha256_text(content.decode("utf-8"))
    assert sha256_file(lf_path) == expected
    assert sha256_file(crlf_path) == expected
    assert sha256_file(lone_cr_path) == expected


def test_civil_code_loader_accepts_equivalent_lf_and_crlf_inputs(tmp_path: Path) -> None:
    lf_content = (
        '{\n  "title": "test",\n  "date": "2020-05-28",\n'
        '  "total_articles": 1,\n  "articles": [\n'
        '    {"number": 1, "content": "article", "book": "", '
        '"chapter": "", "section": "", "sub_book": ""}\n  ]\n}\n'
    )
    expected = sha256_text(lf_content)

    for filename, content in (
        ("lf.json", lf_content),
        ("crlf.json", lf_content.replace("\n", "\r\n")),
    ):
        path = tmp_path / filename
        path.write_bytes(content.encode("utf-8"))

        loaded = load_civil_code(path, expected_sha256=expected)

        assert loaded.source_sha256 == expected
        assert loaded.payload.total_articles == 1


def test_generated_json_and_jsonl_are_written_with_lf_only(tmp_path: Path) -> None:
    json_path = tmp_path / "manifest.json"
    jsonl_path = tmp_path / "records.jsonl"

    write_json(json_path, {"name": "test", "count": 1})
    write_jsonl(jsonl_path, [{"name": "first"}, {"name": "second"}])

    assert json_path.read_bytes() == b'{\n  "name": "test",\n  "count": 1\n}\n'
    assert jsonl_path.read_bytes() == b'{"name":"first"}\n{"name":"second"}\n'
    assert b"\r" not in json_path.read_bytes()
    assert b"\r" not in jsonl_path.read_bytes()
