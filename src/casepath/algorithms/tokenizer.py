from __future__ import annotations

import re

_CHINESE_SEQUENCE = re.compile(r"[\u4e00-\u9fff]+")
_ENGLISH_OR_NUMBER = re.compile(r"[a-z]+|\d+(?:\.\d+)?")
_WHITESPACE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """统一文本大小写并移除空白，减少格式差异对检索结果的影响。"""

    return _WHITESPACE.sub("", text.strip().lower())


def tokenize_zh_bigrams(text: str) -> list[str]:
    """生成中文字符二元组，并保留英文单词与完整数字。

    第一版使用字符二元组而不是外部分词器，目的是让离线评测不受
    分词词典和依赖版本影响。标点会自然分隔中文片段，因此不会产生
    跨越标点的虚假二元组。
    """

    normalized = normalize_text(text)
    if not normalized:
        return []

    tokens: list[str] = []
    for part in _CHINESE_SEQUENCE.findall(normalized):
        if len(part) == 1:
            # 单字查询虽然无法构成二元组，但不能因此失去全部召回能力。
            tokens.append(part)
            continue

        tokens.extend(part[index : index + 2] for index in range(len(part) - 1))

    # 英文缩写、金额和法条编号使用完整形式，避免数字被逐字符拆散。
    tokens.extend(_ENGLISH_OR_NUMBER.findall(normalized))
    return tokens
