from pathlib import Path

import pytest

from casepath.contracts import ProvisionRecord
from casepath.ingestion.laws.jsonl import read_jsonl
from casepath.rule_layer import authority_compare
from casepath.rule_layer.authority_compare import (
    compare_official_document,
    parse_official_articles,
)

COURT_URL = "https://www.court.gov.cn/zixun/xiangqing/233181.html"
STATS_URL = "https://www.stats.gov.cn/gk/tjfg/xgfxfg/202503/t20250312_1958939.html"


def test_official_parser_excludes_headings_footer_and_mobile_duplicate() -> None:
    document = """<div class="txt-content">
      <p><b>第一条</b> 正文 一。</p><p>第 二段。</p>
      <p>第二分编 分编标题</p><p>第一章 章节标题</p>
      <p>第二条 正文二。</p><p>附 则</p>
      <p>第三条 最后正文。</p><p>最后正文的补充段落。</p>
      </div><footer>页面说明</footer>
      <div class="mobile-news-content"><p>第一条 移动副本</p></div>""".encode()
    assert parse_official_articles(document, STATS_URL, expected_count=3) == {
        1: "正文一。第二段。",
        2: "正文二。",
        3: "最后正文。最后正文的补充段落。",
    }


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("<p>第一条 一。</p><p>第三条 三。</p>", "numbers"),
        ("<p>第一条 一。</p><p>第一条 重复。</p>", "duplicate"),
        ("<p>第二条 二。</p><p>第一条 一。</p>", "numbers"),
        ("<p>第一条 </p><p>第二条 二。</p>", "empty"),
    ],
)
def test_official_parser_rejects_missing_duplicate_reordered_or_empty_articles(
    body: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_official_articles(
            f'<div id="zoom">{body}</div>'.encode(), COURT_URL, expected_count=2
        )


@pytest.mark.parametrize(
    "document",
    [
        '<div id="other"><p>第一条 正文。</p></div>',
        '<div id="zoom"><p>第一条 正文。</p>',
        '<div id="zoom"><p>第一条 正文。</p></div><div id="zoom"></div>',
    ],
)
def test_official_parser_rejects_changed_page_container(document: str) -> None:
    with pytest.raises(ValueError, match="container"):
        parse_official_articles(document.encode(), COURT_URL, expected_count=1)


def test_comparison_reports_real_text_changes_but_ignores_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_path = Path(__file__).resolve().parents[2] / "data/canonical/rules/provisions.jsonl"
    provisions = read_jsonl(data_path, ProvisionRecord)[:2]
    monkeypatch.setattr(authority_compare, "EXPECTED_ARTICLE_COUNT", 2)
    expected = {
        int(provision.article_no): "".join(provision.text.split()) for provision in provisions
    }
    monkeypatch.setattr(authority_compare, "parse_official_articles", lambda *args: expected)
    provisions[0].text = " \n" + provisions[0].text + "\t"
    assert compare_official_document(b"official", COURT_URL, provisions)["status"] == "passed"
    provisions[1].text += "正文发生变化"
    report = compare_official_document(b"official", COURT_URL, provisions)
    assert report["status"] == "failed"
    assert report["mismatched_article_numbers"] == [2]
