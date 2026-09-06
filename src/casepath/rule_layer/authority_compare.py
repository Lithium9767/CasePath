"""Recheck the published Civil Code against official HTML without changing the release."""

from __future__ import annotations

import argparse
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from casepath.contracts import ProvisionRecord
from casepath.ingestion.laws.civil_code import EXPECTED_ARTICLE_COUNT
from casepath.ingestion.laws.jsonl import read_jsonl, sha256_bytes, sha256_text

OFFICIAL_SOURCES = {
    "https://www.court.gov.cn/zixun/xiangqing/233181.html": ("id", "zoom"),
    "https://www.stats.gov.cn/gk/tjfg/xgfxfg/202503/t20250312_1958939.html": (
        "class",
        "txt-content",
    ),
}
_DIGITS = "零一二三四五六七八九"
_UNITS = {"十": 10, "百": 100, "千": 1000}
_ARTICLE = re.compile(r"^第([零一二三四五六七八九十百千]+)条\s*(.*)$")
_HEADING = re.compile(r"^第[零一二三四五六七八九十百千]+(?:分?编|章|节)|^附\s*则$")
_BLOCK_TAGS = {"p", "div", "br", "h1", "h2", "h3", "li"}


class _OfficialBody(HTMLParser):
    """Select one article container, excluding navigation and mobile duplicate copies."""

    def __init__(self, attribute: str, value: str) -> None:
        super().__init__(convert_charrefs=True)
        self.attribute = attribute
        self.value = value
        self.depth = 0
        self.matches = 0
        self.ignored = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "div":
            if self.depth:
                self.depth += 1
            elif self.value in (dict(attrs).get(self.attribute) or "").split():
                self.depth = 1
                self.matches += 1
        if self.depth:
            if tag in {"script", "style"}:
                self.ignored += 1
            if tag in _BLOCK_TAGS:
                self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.depth:
            if tag in {"script", "style"}:
                self.ignored = max(0, self.ignored - 1)
            if tag in _BLOCK_TAGS:
                self.parts.append("\n")
            if tag == "div":
                self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth and not self.ignored:
            self.parts.append(data)


def _article_number(label: str) -> int:
    total = current = 0
    for character in label:
        if character in _DIGITS:
            current = _DIGITS.index(character)
        else:
            total += (current or 1) * _UNITS[character]
            current = 0
    return total + current


def parse_official_articles(
    document: bytes, source_url: str, *, expected_count: int = EXPECTED_ARTICLE_COUNT
) -> dict[int, str]:
    parser = _OfficialBody(*OFFICIAL_SOURCES[source_url])
    parser.feed(document.decode("utf-8-sig"))
    parser.close()
    if parser.matches != 1 or parser.depth:
        raise ValueError("official article container is missing, duplicated or unclosed")
    articles: dict[int, list[str]] = {}
    current: int | None = None
    for raw_line in "".join(parser.parts).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _ARTICLE.match(line)
        if match:
            current = _article_number(match[1])
            if current in articles:
                raise ValueError(f"duplicate official article: {current}")
            articles[current] = [match[2]]
        elif _HEADING.match(line):
            current = None
        elif current is not None:
            articles[current].append(line)
    if list(articles) != list(range(1, expected_count + 1)):
        raise ValueError("official article numbers are missing, out of order or unexpected")
    normalized = {number: "".join("".join(parts).split()) for number, parts in articles.items()}
    if not all(normalized.values()):
        raise ValueError("official article has empty text")
    return normalized


def compare_official_document(
    document: bytes, source_url: str, provisions: list[ProvisionRecord]
) -> dict[str, object]:
    official = parse_official_articles(document, source_url)
    numbers = [int(provision.article_no) for provision in provisions]
    if numbers != list(range(1, EXPECTED_ARTICLE_COUNT + 1)):
        raise ValueError("canonical article numbers must be unique, complete and ordered")
    mismatches = [
        number
        for number, provision in zip(numbers, provisions, strict=True)
        if official[number] != "".join(provision.text.split())
    ]
    return {
        "source_url": source_url,
        "document_sha256": sha256_bytes(document),
        "compared_article_count": len(official),
        "normalized_corpus_sha256": sha256_text(
            "\n".join(f"{number}\t{body}" for number, body in official.items())
        ),
        "mismatched_article_numbers": mismatches,
        "status": "passed" if not mismatches else "failed",
    }


def fetch_official_document(source_url: str) -> bytes:
    if source_url not in OFFICIAL_SOURCES:
        raise ValueError("unsupported official source")
    request = Request(source_url, headers={"User-Agent": "CasePath-source-review/1.0"})
    with urlopen(request, timeout=25) as response:
        resolved = urlparse(response.url)
        if resolved.scheme != "https" or resolved.hostname != urlparse(source_url).hostname:
            raise ValueError("official source redirected to an unexpected host")
        document = response.read(6 * 1024 * 1024 + 1)
    if len(document) > 6 * 1024 * 1024:
        raise ValueError("official document exceeds size limit")
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    args = parser.parse_args()
    provisions = read_jsonl(args.data_root / "canonical/rules/provisions.jsonl", ProvisionRecord)
    reports = []
    for source_url in OFFICIAL_SOURCES:
        try:
            reports.append(
                compare_official_document(
                    fetch_official_document(source_url), source_url, provisions
                )
            )
        except (OSError, ValueError) as error:
            reports.append({"source_url": source_url, "status": "failed", "error": str(error)})
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    if any(report["status"] != "passed" for report in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
