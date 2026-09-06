"""Split judgment text into party statements, court findings, and the holding."""

from __future__ import annotations

import re
from dataclasses import dataclass

SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
DATE_RE = re.compile(r"(\d{4})[.年/-](\d{1,2})[.月/-](\d{1,2})")
AMOUNT_RE = re.compile(r"(?:人民币)?(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*元")
_REQUESTED_AMOUNT_RE = re.compile(
    r"(?:返还|退还|退款|款项).{0,40}?" + AMOUNT_RE.pattern
)
_GRANTED_AMOUNT_RE = re.compile(r"(?:返还|支付|给付).{0,40}?" + AMOUNT_RE.pattern)
COURT_FINDING_RE = re.compile(r"(法院经审理查明|经审理查明|法院认定)[：:]")
PLAINTIFF_RE = re.compile(r"(?:原告[^。\n]{0,20})?诉称[：:]")
DEFENDANT_RE = re.compile(r"(?:被告[^。\n]{0,30})?辩称[：:]")
HOLDING_RE = re.compile(
    r"(?:人民法院|法院).{0,40}作出.{0,40}判决[：:]|"
    r"(?:判决|判决如下)[：:]"
)
SENTENCE_RE = re.compile(r"[^。；\n]+[。；]?")


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
    holding_start = _first_span(facts, HOLDING_RE)
    court = ""
    holding = ""
    if court_start is not None:
        court_end = len(facts)
        if holding_start is not None and holding_start[0] > court_start[0]:
            court_end = holding_start[0]
        court = facts[court_start[1] : court_end].strip()
    if holding_start is not None:
        holding = facts[holding_start[1] :].strip()
    if not holding:
        holding = ""
    return FactPartitions(
        plaintiff=plaintiff,
        defendant=defendant,
        court=court,
        holding=holding,
    )


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
    return float(match.group(1).replace(",", ""))


def parse_requested_amount(plaintiff_text: str) -> float | None:
    match = _REQUESTED_AMOUNT_RE.search(plaintiff_text or "")
    if match is not None:
        return float(match.group(1).replace(",", ""))
    return parse_amount(plaintiff_text)


def parse_granted_amount(holding_text: str) -> float | None:
    match = _GRANTED_AMOUNT_RE.search(holding_text or "")
    if match is not None:
        return float(match.group(1).replace(",", ""))
    return parse_amount(holding_text)


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
