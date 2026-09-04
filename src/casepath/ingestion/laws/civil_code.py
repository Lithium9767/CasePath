from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import Field, model_validator

from casepath.contracts import (
    ContractModel,
    LegalSourceRecord,
    MaturityLevel,
    ProvisionRecord,
    SourceSpan,
)

from .jsonl import sha256_file, sha256_text

CIVIL_CODE_SOURCE_ID = "law.prc.civil_code.2021"
EXPECTED_ARTICLE_COUNT = 1260
# SHA-256 values of the pinned Git blobs after canonicalising text newlines to
# LF.  The same values are produced from a Windows CRLF checkout.
EXPECTED_SOURCE_SHA256 = "405e960cec922c11c466bebfd0ddc3baca11cd2678861ac11e7590b75c0453de"
EXPECTED_STATS_SHA256 = "8e0d6d501651d2e5a0cc23d45deeb8e7451671ef6831a2184662f66d7f40f3eb"
EXPECTED_UPSTREAM_REVISION = "ce7872c7ae343e5ff860d627195ec4e72c7ef7ce"
EXPECTED_HIERARCHY_REPAIR_COUNT = 109
OFFICIAL_SOURCE_URL = "https://wb.flk.npc.gov.cn/flfg/PDF/bd53dd912c1048f2aecbaa229238334b.pdf"

HIERARCHY_FIELDS = ("book", "sub_book", "chapter", "section")


class RawCivilCodeArticle(ContractModel):
    number: int = Field(ge=1)
    content: str = Field(min_length=1)
    book: str = ""
    chapter: str = ""
    section: str = ""
    sub_book: str = ""


class RawCivilCodePayload(ContractModel):
    title: str = Field(min_length=1)
    date: str = Field(min_length=1)
    total_articles: int = Field(ge=1)
    articles: list[RawCivilCodeArticle] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_articles(self) -> RawCivilCodePayload:
        numbers = [article.number for article in self.articles]
        if self.total_articles != len(self.articles):
            raise ValueError("declared total_articles does not match articles length")
        if numbers != list(range(1, self.total_articles + 1)):
            raise ValueError("article numbers must be unique and contiguous from 1")
        return self


@dataclass(frozen=True)
class LoadedCivilCode:
    payload: RawCivilCodePayload
    source_sha256: str


@dataclass(frozen=True)
class ConversionResult:
    legal_source: LegalSourceRecord
    provisions: list[ProvisionRecord]
    source_spans: list[SourceSpan]
    hierarchy_repair_count: int
    source_sha256: str


def load_civil_code(
    source_path: Path,
    *,
    expected_sha256: str | None = EXPECTED_SOURCE_SHA256,
) -> LoadedCivilCode:
    source_sha256 = sha256_file(source_path)
    if expected_sha256 is not None and source_sha256 != expected_sha256:
        raise ValueError(
            "unexpected civil-code source hash: "
            f"expected {expected_sha256}, received {source_sha256}"
        )
    payload = RawCivilCodePayload.model_validate_json(source_path.read_bytes())
    return LoadedCivilCode(payload=payload, source_sha256=source_sha256)


def _hierarchy(article: RawCivilCodeArticle) -> tuple[str, str, str, str]:
    return tuple(getattr(article, field) for field in HIERARCHY_FIELDS)  # type: ignore[return-value]


def repair_shifted_hierarchy(
    articles: list[RawCivilCodeArticle],
    *,
    source_sha256: str,
) -> tuple[list[RawCivilCodeArticle], int]:
    """Repair the known one-record-early hierarchy bug in the pinned upstream file.

    The upstream DOCX converter updates hierarchy state before flushing the preceding
    article.  Repairs are therefore derived from an immutable snapshot; using already
    repaired values here would propagate the previous hierarchy through the dataset.
    """

    if source_sha256 != EXPECTED_SOURCE_SHA256:
        raise ValueError("hierarchy repair is only defined for the pinned upstream input")

    hierarchy_snapshot = [_hierarchy(article) for article in articles]
    repaired: list[RawCivilCodeArticle] = []
    repair_count = 0
    for index, article in enumerate(articles):
        if index and hierarchy_snapshot[index] != hierarchy_snapshot[index - 1]:
            previous = hierarchy_snapshot[index - 1]
            updates = dict(zip(HIERARCHY_FIELDS, previous, strict=True))
            article = article.model_copy(update=updates)
            repair_count += 1
        repaired.append(article)
    return repaired, repair_count


def _normalise_heading(value: str) -> str | None:
    if not value:
        return None
    compact = re.sub(r"\s+", "", value)
    match = re.match(r"^(第.+?(?:分编|编|章|节))(.*)$", compact)
    if match and match.group(2):
        return f"{match.group(1)} {match.group(2)}"
    return compact


def provision_id(article_number: int) -> str:
    return f"{CIVIL_CODE_SOURCE_ID}.article_{article_number:04d}"


def full_span_id(article_number: int) -> str:
    # This spelling is already frozen in the demo workflow and cross-team examples.
    return f"span.civil-code.{article_number}"


def _canonical_content_hash(articles: list[RawCivilCodeArticle]) -> str:
    content = "\n".join(f"{article.number}\t{article.content}" for article in articles)
    return sha256_text(content)


def convert_civil_code(loaded: LoadedCivilCode) -> ConversionResult:
    articles, repair_count = repair_shifted_hierarchy(
        loaded.payload.articles,
        source_sha256=loaded.source_sha256,
    )
    if len(articles) == EXPECTED_ARTICLE_COUNT and repair_count != EXPECTED_HIERARCHY_REPAIR_COUNT:
        raise ValueError(
            "unexpected hierarchy repair count: "
            f"expected {EXPECTED_HIERARCHY_REPAIR_COUNT}, received {repair_count}"
        )

    legal_source = LegalSourceRecord(
        source_id=CIVIL_CODE_SOURCE_ID,
        title="中华人民共和国民法典",
        authority="全国人民代表大会",
        document_type="法律",
        promulgated_on=date(2020, 5, 28),
        valid_from=date(2021, 1, 1),
        valid_to=None,
        effective_status="effective",
        jurisdiction="中华人民共和国",
        official_source_url=OFFICIAL_SOURCE_URL,
        content_hash=_canonical_content_hash(articles),
    )

    provisions: list[ProvisionRecord] = []
    spans: list[SourceSpan] = []
    for article in articles:
        span_id = full_span_id(article.number)
        spans.append(
            SourceSpan(
                span_id=span_id,
                source_id=CIVIL_CODE_SOURCE_ID,
                section=f"第{article.number}条",
                paragraph_id=f"article-{article.number:04d}",
                start_offset=0,
                end_offset=len(article.content),
                quote=article.content,
                content_hash=sha256_text(article.content),
            )
        )
        provisions.append(
            ProvisionRecord(
                provision_id=provision_id(article.number),
                source_id=CIVIL_CODE_SOURCE_ID,
                article_no=str(article.number),
                title=f"中华人民共和国民法典第{article.number}条",
                text=article.content,
                book=_normalise_heading(article.book),
                sub_book=_normalise_heading(article.sub_book),
                chapter=_normalise_heading(article.chapter),
                section=_normalise_heading(article.section),
                valid_from=date(2021, 1, 1),
                valid_to=None,
                effective_status="effective",
                jurisdiction="中华人民共和国",
                maturity=MaturityLevel.L0,
                source_span_ids=[span_id],
                content_hash=sha256_text(article.content),
            )
        )

    return ConversionResult(
        legal_source=legal_source,
        provisions=provisions,
        source_spans=spans,
        hierarchy_repair_count=repair_count,
        source_sha256=loaded.source_sha256,
    )
