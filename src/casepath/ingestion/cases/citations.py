"""Resolve upstream case citations to official Civil Code provision IDs."""

from __future__ import annotations

import re
from typing import Any

from casepath.ingestion.laws.civil_code import provision_id

CIVIL_CODE_ARTICLE_MAX = 1260
_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}
_NON_STATUTE_MARKERS = ("解释", "规定", "指导", "批复", "纪要", "意见", "通知")
_LEADING_ARTICLE_RE = re.compile(r"^第")
_TRAILING_ARTICLE_RE = re.compile(r"[条款项].*$")


def is_civil_code_statute(law_name: str) -> bool:
    """Return True only for the Civil Code statute itself, not related JIs."""

    name = law_name.strip()
    if "民法典" not in name:
        return False
    return not any(marker in name for marker in _NON_STATUTE_MARKERS)


def parse_chinese_int(text: str) -> int | None:
    if not text or any(char not in _CN_DIGITS and char not in _CN_UNITS for char in text):
        return None
    total = 0
    current = 0
    for char in text:
        if char in _CN_DIGITS:
            current = _CN_DIGITS[char]
            continue
        unit = _CN_UNITS[char]
        if current == 0:
            current = 1
        total += current * unit
        current = 0
    total += current
    return total if total > 0 else None


def parse_article_number(raw: object) -> int | None:
    text = _TRAILING_ARTICLE_RE.sub("", _LEADING_ARTICLE_RE.sub("", str(raw or "").strip()))
    text = text.replace(" ", "")
    if not text:
        return None
    if text.isdigit():
        number = int(text)
    else:
        number = parse_chinese_int(text)
    if number is None or number < 1 or number > CIVIL_CODE_ARTICLE_MAX:
        return None
    return number


def resolve_civil_code_provision_id(law_name: object, article: object) -> str | None:
    if not is_civil_code_statute(str(law_name or "")):
        return None
    number = parse_article_number(article)
    if number is None:
        return None
    return provision_id(number)


def related_provision_ids(case: dict[str, Any]) -> list[str]:
    resolved: set[str] = set()
    for item in case.get("legal_provisions") or []:
        if not isinstance(item, dict):
            continue
        provision = resolve_civil_code_provision_id(item.get("law_name"), item.get("article"))
        if provision is not None:
            resolved.add(provision)
    return sorted(resolved)
