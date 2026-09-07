from casepath.ingestion.cases.citations import (
    parse_article_number,
    parse_chinese_int,
    related_provision_ids,
    related_provision_ids_from_text,
    resolve_civil_code_provision_id,
)
from casepath.ingestion.laws.civil_code import provision_id


def test_chinese_and_arabic_articles_convert_in_range():
    assert parse_chinese_int("九") == 9
    assert parse_chinese_int("二十六") == 26
    assert parse_chinese_int("五百零九") == 509
    assert parse_chinese_int("一千二百二十九") == 1229
    assert parse_chinese_int("一千二百六十") == 1260
    assert parse_article_number("509") == 509
    assert parse_article_number("第563条") == 563
    assert parse_article_number("一千二百六十一") is None
    assert parse_article_number("0") is None


def test_judicial_interpretations_are_not_rewritten_as_civil_code_ids():
    assert (
        resolve_civil_code_provision_id(
            "最高人民法院关于适用《中华人民共和国民法典》时间效力的若干规定",
            "2",
        )
        is None
    )
    assert resolve_civil_code_provision_id("中华人民共和国行政诉讼法", "二") is None
    assert resolve_civil_code_provision_id("中华人民共和国民法典", "二") == provision_id(2)


def test_related_provision_ids_deduplicate_and_ignore_unresolved():
    case = {
        "legal_provisions": [
            {"law_name": "中华人民共和国民法典", "article": "五百零九"},
            {"law_name": "中华人民共和国民法典", "article": "509"},
            {"law_name": "中华人民共和国行政诉讼法", "article": "二"},
            {
                "law_name": "最高人民法院关于适用《中华人民共和国民法典》婚姻家庭编的解释（一）",
                "article": "法释〔2020〕22号",
            },
        ]
    }
    assert related_provision_ids(case) == [provision_id(509)]


def test_related_provision_ids_from_text_require_civil_code_name():
    assert related_provision_ids_from_text("协议第一条借款本金") == []
    assert related_provision_ids_from_text(
        "原告依据《中华人民共和国民法典》第五百六十三条请求解除合同。"
    ) == [provision_id(563)]
