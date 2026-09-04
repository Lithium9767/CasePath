from casepath.algorithms.tokenizer import normalize_text, tokenize_zh_bigrams


def test_normalize_text_removes_whitespace_and_normalizes_english_case():
    """空白和英文大小写不应导致检索词元发生变化。"""

    assert normalize_text("  合同  API\n检索  ") == "合同api检索"


def test_tokenize_chinese_phrase_as_bigrams():
    """连续中文文本应切分为相邻的字符二元组。"""

    assert tokenize_zh_bigrams("合同解除") == ["合同", "同解", "解除"]


def test_tokenize_single_chinese_character():
    """单个汉字无法组成二元组，但仍需保留以支持极短查询。"""

    assert tokenize_zh_bigrams("法") == ["法"]


def test_tokenize_preserves_english_words_and_numbers():
    """英文缩写、金额和法条编号应作为完整词元参与检索。"""

    assert tokenize_zh_bigrams("API 第563条 5000.50元") == [
        "第",
        "条",
        "元",
        "api",
        "563",
        "5000.50",
    ]


def test_tokenize_ignores_punctuation_and_extra_whitespace():
    """标点和多余空白不应制造无意义词元。"""

    assert tokenize_zh_bigrams("合同， 解除！") == ["合同", "解除"]


def test_tokenize_empty_text_returns_empty_list():
    """空文本应安全返回空列表。"""

    assert tokenize_zh_bigrams("") == []
    assert tokenize_zh_bigrams(" \n\t ") == []


def test_tokenize_is_deterministic():
    """相同输入必须始终产生相同词元序列。"""

    text = "健身房停止经营，要求退还3000元余额。"
    assert tokenize_zh_bigrams(text) == tokenize_zh_bigrams(text)
