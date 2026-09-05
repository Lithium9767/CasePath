from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from casepath.ingestion.laws.civil_code import (
    CIVIL_CODE_SOURCE_ID,
    EXPECTED_HIERARCHY_REPAIR_COUNT,
    EXPECTED_SOURCE_SHA256,
    LoadedCivilCode,
    RawCivilCodeArticle,
    RawCivilCodePayload,
    convert_civil_code,
    load_civil_code,
    repair_shifted_hierarchy,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
UPSTREAM_SOURCE = REPOSITORY_ROOT.parent / "legal-rag" / "data" / "laws" / "民法典_法条.json"


def _ten_article_sample() -> LoadedCivilCode:
    articles = [
        RawCivilCodeArticle(
            number=number,
            content=f"第{number}条测试正文。",
            book="第一编 总  则",
            chapter="第一章 基本规定",
        )
        for number in range(1, 11)
    ]
    payload = RawCivilCodePayload(
        title="中华人民共和国民法典",
        date="2020年5月28日",
        total_articles=len(articles),
        articles=articles,
    )
    return LoadedCivilCode(payload=payload, source_sha256=EXPECTED_SOURCE_SHA256)


def test_ten_article_sample_converts_to_canonical_records() -> None:
    result = convert_civil_code(_ten_article_sample())

    assert result.legal_source.source_id == CIVIL_CODE_SOURCE_ID
    assert result.legal_source.source_type == "LAW"
    assert result.legal_source.authority == "全国人民代表大会"
    assert result.legal_source.jurisdiction == "中华人民共和国"
    assert result.legal_source.effective_from == date(2021, 1, 1)
    assert result.legal_source.effective_to is None
    assert result.legal_source.official_url is not None
    assert result.hierarchy_repair_count == 0
    assert len(result.provisions) == 10
    assert len(result.source_spans) == 10
    assert result.provisions[0].provision_id.endswith("article_0001")
    assert result.provisions[-1].provision_id.endswith("article_0010")
    assert [record.article_no for record in result.provisions] == [str(i) for i in range(1, 11)]

    for provision, span in zip(result.provisions, result.source_spans, strict=True):
        assert provision.source_spans == [span]
        assert provision.contract_version == "1.1"
        assert provision.effective_from == date(2021, 1, 1)
        assert provision.effective_to is None
        assert provision.article_no.isdecimal()
        assert span.start_offset == 0
        assert span.end_offset == len(provision.text)
        assert provision.text[span.start_offset : span.end_offset] == span.quote
        assert span.paragraph_id == f"article-{int(provision.article_no):04d}"


def test_hierarchy_repair_uses_an_immutable_snapshot() -> None:
    articles = [
        RawCivilCodeArticle(number=1, content="一。", book="第一编"),
        RawCivilCodeArticle(number=2, content="二。", book="第二编"),
        RawCivilCodeArticle(number=3, content="三。", book="第二编"),
    ]

    repaired, count = repair_shifted_hierarchy(
        articles,
        source_sha256=EXPECTED_SOURCE_SHA256,
    )

    assert count == 1
    assert [article.book for article in repaired] == ["第一编", "第一编", "第二编"]
    assert [article.book for article in articles] == ["第一编", "第二编", "第二编"]


@pytest.mark.skipif(not UPSTREAM_SOURCE.exists(), reason="external legal-rag checkout is absent")
def test_pinned_upstream_source_hash_and_full_hierarchy_repair() -> None:
    loaded = load_civil_code(UPSTREAM_SOURCE)
    result = convert_civil_code(loaded)

    assert loaded.source_sha256 == EXPECTED_SOURCE_SHA256
    assert loaded.payload.total_articles == 1260
    assert result.hierarchy_repair_count == EXPECTED_HIERARCHY_REPAIR_COUNT == 109

    repaired, repair_count = repair_shifted_hierarchy(
        loaded.payload.articles,
        source_sha256=loaded.source_sha256,
    )
    assert repair_count == EXPECTED_HIERARCHY_REPAIR_COUNT
    by_number = {article.number: article for article in repaired}
    expected_boundaries = {
        204: "第一编 总则",
        205: "第二编 物权",
        462: "第二编 物权",
        463: "第三编 合同",
        988: "第三编 合同",
        989: "第四编 人格权",
        1039: "第四编 人格权",
        1040: "第五编 婚姻家庭",
        1118: "第五编 婚姻家庭",
        1119: "第六编 继承",
        1163: "第六编 继承",
        1164: "第七编 侵权责任",
        1258: "第七编 侵权责任",
        1259: "附则",
        1260: "附则",
    }
    assert {
        number: re.sub(r"\s+", "", by_number[number].book) for number in expected_boundaries
    } == {number: re.sub(r"\s+", "", heading) for number, heading in expected_boundaries.items()}


def test_load_rejects_an_unpinned_source(tmp_path: Path) -> None:
    source = tmp_path / "civil-code.json"
    source.write_text(
        '{"title":"测试","date":"2020年5月28日","total_articles":1,'
        '"articles":[{"number":1,"content":"测试。","book":"第一编",'
        '"chapter":"","section":"","sub_book":""}]}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unexpected civil-code source hash"):
        load_civil_code(source)
