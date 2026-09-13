"""Split judgment text into party statements, court findings, and the holding."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
DATE_RE = re.compile(r"(\d{4})[.年/-](\d{1,2})[.月/-](\d{1,2})")
AMOUNT_RE = re.compile(
    r"(?<![\d.,，．])(?:人民币)?"
    r"(?P<amount>(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:[.．]\d+)?)"
    r"\s*(?P<unit>万|亿)?元"
)
_REQUESTED_AMOUNT_RE = re.compile(
    r"(?:请求(?:法院)?判令|诉讼请求|诉请|请求|要求|主张)"
    r"[^。\n]{0,240}?" + AMOUNT_RE.pattern
)
_GRANTED_AMOUNT_RE = re.compile(
    r"(?:返还|退还|支付|给付|赔偿|归还|偿还|补偿)"
    r"[^。；;\n]{0,100}?" + AMOUNT_RE.pattern
)
_TOTAL_AMOUNT_RE = re.compile(r"(?:合计|共计|总计)\s*" + AMOUNT_RE.pattern)
_PERIODIC_PREFIX_RE = re.compile(
    r"(?:每(?:年|月|日|天|周|季|次|小时)|按(?:年|月|日|天|周|季))[^。；;\n]{0,24}$"
)
_POST_DISPOSITION_RE = re.compile(
    r"宣判后|判决作出后|(?:一审)?判决生效后[，,]|本案未上诉"
)
_COST_CLAUSE_RE = re.compile(
    r"^\s*(?:[一二三四五六七八九十\d]+[.、]\s*)?(?:本案)?(?:诉讼费|案件受理费)"
)
COURT_FINDING_RE = re.compile(r"(法院经审理查明|经审理查明|法院认定)[：:]")
PLAINTIFF_RE = re.compile(r"(?:原告[^。\n]{0,20})?诉称[：:，,]")
DEFENDANT_RE = re.compile(r"(?:被告[^。\n]{0,30})?辩称[：:，,]")
HOLDING_RE = re.compile(
    r"(?:人民法院|法院)[^。\n]{0,80}?(?:作出|做出)[^。\n]{0,80}?判决[：:，,]|"
    r"(?:作出|做出)[^。\n]{0,80}?判决[：:，,]|"
    r"(?:判决|判决如下)[：:]"
)
SENTENCE_RE = re.compile(r"[^。；\n]+[。；]?")
NUMBERED_FACT_RE = re.compile(r"(?m)^\s*\d+\.\s+")


@dataclass(frozen=True)
class FactPartitions:
    plaintiff: str
    defendant: str
    court: str
    holding: str


def split_sections(markdown: str) -> dict[str, str]:
    matches = list(SECTION_RE.finditer(markdown))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        sections[match.group(1).strip()] = markdown[start:end].strip()
    return sections


def split_fact_partitions(facts: str) -> FactPartitions:
    plaintiff = _slice_after(facts, PLAINTIFF_RE, (DEFENDANT_RE, COURT_FINDING_RE, HOLDING_RE))
    defendant = _slice_after(facts, DEFENDANT_RE, (COURT_FINDING_RE, HOLDING_RE, PLAINTIFF_RE))
    court_start = _first_span(facts, COURT_FINDING_RE)
    # A prior proceeding may be recited in the findings. The last explicit
    # judgment is the current disposition; an affirmance alone stays unknown.
    holdings = []
    for match in HOLDING_RE.finditer(facts):
        prefix = re.split(r"[。；;\n]", facts[:match.start()])[-1]
        if re.search(r"(?:请求|诉请|要求)(?:人民法院|法院)?(?:依法)?$", prefix):
            continue
        if (
            "作出" not in match.group(0)
            and "做出" not in match.group(0)
            and re.search(r"请求|诉请|要求", prefix)
        ):
            continue
        holdings.append(match)
    holding_start = holdings[-1] if holdings else None
    court = ""
    holding = ""
    if court_start is not None:
        court_end = len(facts)
        court_holdings = [match for match in holdings if match.start() > court_start[0]]
        if court_holdings:
            court_end = court_holdings[0].start()
        court = facts[court_start[1] : court_end].strip()
    if holding_start is not None:
        holding = facts[holding_start.end() :].strip()
    return FactPartitions(
        plaintiff=plaintiff,
        defendant=defendant,
        court=court,
        holding=holding,
    )


def numbered_fact_items(text: str) -> list[str]:
    """Split LexRel-style court-recognized facts that start with ``1.`` ``2.``."""

    matches = list(NUMBERED_FACT_RE.finditer(text or ""))
    if not matches:
        return []
    items: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        quote = text[match.start() : end].strip()
        if len(quote) >= 8:
            items.append(quote)
    return items


def court_finding_sentences(court_text: str) -> list[str]:
    sentences: list[str] = []
    for match in SENTENCE_RE.finditer(court_text):
        sentence = match.group(0).strip()
        if len(sentence) < 8:
            continue
        if not sentence.endswith(("。", "；")):
            continue
        if HOLDING_RE.search(sentence) or "民事判决" in sentence:
            continue
        sentences.append(sentence)
    return sentences


def parse_amount(text: str) -> float | None:
    match = AMOUNT_RE.search(text or "")
    if match is None:
        return None
    return _amount_value(match)


def parse_requested_amount(plaintiff_text: str) -> float | None:
    match = _REQUESTED_AMOUNT_RE.search(plaintiff_text or "")
    if match is not None:
        demand = re.split(r"[。\n]", plaintiff_text[match.start():], maxsplit=1)[0]
        demand = "；".join(
            clause for clause in re.split(r"[；;]", demand) if not _COST_CLAUSE_RE.search(clause)
        )
        return _unambiguous_amount(demand)
    # Contract prices, payments already made, and loan principal in a service
    # relation are not an identified litigation demand.
    return None


def parse_granted_amount(holding_text: str) -> float | None:
    operative = _POST_DISPOSITION_RE.split(holding_text or "", maxsplit=1)[0]
    clauses = []
    for sentence in SENTENCE_RE.finditer(operative):
        text = sentence.group(0)
        if re.search(r"驳回|不予支持|无需|无须|不应支付|诉讼费|案件受理费", text):
            continue
        clauses.append(text)
    scope = "\n".join(clauses)
    if not _GRANTED_AMOUNT_RE.search(scope):
        return None
    return _unambiguous_amount(scope)


def _unambiguous_amount(text: str) -> float | None:
    amounts = list(AMOUNT_RE.finditer(text))
    if not amounts:
        return None
    totals = list(_TOTAL_AMOUNT_RE.finditer(text))
    if totals:
        # A subtotal of interest or a later independent request is not a case
        # total. Do not add figures or pick one from several competing totals.
        if len(totals) != 1:
            return None
        chosen = totals[0]
        value = _amount_value(chosen)
        if len(amounts) > 1 and re.search(
            r"(?:利息|罚息|复利)(?:[（(][^）)]{0,40}[）)])?$", text[:chosen.start()]
        ):
            return None
        if amounts[-1].end() != chosen.end():
            return None
        if any(_amount_value(amount) > value for amount in amounts):
            return None
    elif len(amounts) == 1:
        chosen = amounts[0]
        value = _amount_value(chosen)
    else:
        return None
    prefix = text[max(0, chosen.start() - 40):chosen.start()]
    if _PERIODIC_PREFIX_RE.search(prefix):
        return None
    return value


def _amount_value(match: re.Match[str]) -> float:
    number = match.group("amount").replace(",", "").replace("，", "").replace("．", ".")
    multiplier = {None: 1, "万": 10_000, "亿": 100_000_000}[match.group("unit")]
    return float(Decimal(number) * multiplier)


def _first_span(text: str, pattern: re.Pattern[str]) -> tuple[int, int] | None:
    match = pattern.search(text)
    if match is None:
        return None
    return match.start(), match.end()


def _slice_after(
    text: str,
    start_pattern: re.Pattern[str],
    stop_patterns: tuple[re.Pattern[str], ...],
) -> str:
    start = _first_span(text, start_pattern)
    if start is None:
        return ""
    stops = [
        match.start()
        for pattern in stop_patterns
        for match in [pattern.search(text, start[1])]
        if match is not None and match.start() > start[1]
    ]
    end = min(stops) if stops else len(text)
    return text[start[1] : end].strip()
