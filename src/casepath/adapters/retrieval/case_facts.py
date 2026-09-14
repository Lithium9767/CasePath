"""Conservative fact-prefix extraction shared by retrieval and evaluation.

This pure text view is not a legal fact/holding classifier. It excludes explicit
reasoning/disposition suffixes, potentially also losing later facts after a
historical judgment. A result of "" is allowed: production can still retrieve
older metadata-only records, while evaluators may require a nonempty prefix.
No source records are modified and no labels or model calls are introduced.
"""

from __future__ import annotations

import re

_EDITORIAL_PREFIX = re.compile(r"^\s*【编辑摘要，非判决原文】\s*")
_SECTION = re.compile(
    r"(?:【(?:裁判理由|裁判结果|判决结果)】|"
    r"^\s*(?:#{1,6}\s*)?(?:裁判理由|裁判结果|判决结果)\s*[：:]?)", re.MULTILINE
)
_OUTCOME = re.compile(
    r"(?:本院|法院)(?:经审理)?认为|"
    r"(?:本院|法院|法庭)(?:依法|遂|最终)?(?:判决|判令|判处|裁定|驳回)|"
    r"(?:一审|二审|终审)(?:法院)?(?:判决|裁定)|"
    r"(?:人民法院|法院|法庭)[^。；;\n]{0,100}?(?:作出|做出)"
    r"[^。；;\n]{0,90}?(?:判决|裁定)|"
    r"判决如下|裁定如下|判决[：:]|裁定[：:]"
)
_PLEADING = re.compile(
    r"(?:请求|诉请|要求)(?:人民法院|法院|本院|法庭)?(?:依法)?"
    r"(?:作出|做出)?(?:民事)?$"
)


def _sentence_start(text: str, position: int) -> int:
    return 1 + max(text.rfind(mark, 0, position) for mark in ("。", "；", ";", "\n"))


def extract_case_fact_prefix(facts_text: str) -> str:
    """Keep a factual prefix; explicit requested judgments remain party requests.

    Text after the first recognized reasoning/disposition boundary is omitted.
    Source facts may still contain legal language or unrecognized outcomes;
    this function does not certify globally leakage-free retrieval documents.
    """

    text = _EDITORIAL_PREFIX.sub("", facts_text.replace("\r\n", "\n")).strip()
    cut = len(text)
    for match in _SECTION.finditer(text):
        cut = min(cut, match.start())
    for match in _OUTCOME.finditer(text):
        start = _sentence_start(text, match.start())
        if _PLEADING.search(text[start:match.start()]):
            continue
        cut = min(cut, start)
    return text[:cut].strip()
