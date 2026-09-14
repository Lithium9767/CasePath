import math
from collections import Counter

import pytest

from casepath.adapters.retrieval.retrieval_catalog import (
    BM25Index,
    _distinct_search_chunks,
    _rule_search_text,
    tokenize,
)
from casepath.contracts import RuleRecord


@pytest.mark.parametrize("separator", ["，", "。", "；", "\n", " ", "125", "word", "_"])
def test_bigrams_never_cross_non_han_boundaries(separator):
    terms = tokenize(f"甲{separator}乙")
    assert "甲" in terms and "乙" in terms
    assert "甲乙" not in terms


def test_contiguous_han_has_local_unigrams_and_bigrams():
    assert tokenize("甲乙丙") == ["甲", "乙", "丙", "甲乙", "乙丙"]
    assert "甲丙" not in tokenize("甲乙丙")


def test_latin_numbers_and_negation_are_not_silently_removed():
    terms = tokenize("不退款 123 ABC_code")
    assert {"不", "不退", "退款", "123", "abc_code"} <= set(terms)
    assert tokenize("！？ ; \n") == []


def test_repeating_a_query_cannot_multiply_its_influence():
    index = BM25Index({"a": "依法支付报酬", "b": "依法返还借款", "c": "借款 借款"})
    assert index.search("借款 返还", 3) == index.search("借款 返还 借款 返还", 3)
    assert index.search("借款。返还", 3) == index.search("借款。返还\n借款。返还", 3)


def test_relative_query_emphasis_is_retained_without_whole_query_amplification():
    index = BM25Index({"a": "alpha", "b": "beta"})
    assert index.search("alpha alpha beta", 2)[0][0] == "a"
    assert index.search("alpha beta beta", 2)[0][0] == "b"
    # The emphasized word remains emphasized after repeating the entire utterance,
    # while neither the ranking nor any numeric score is inflated.
    original = index.search("alpha beta beta", 2)
    assert original == index.search("alpha beta beta\nalpha beta beta", 2)
    assert original == index.search("alpha beta beta " * 10, 2)


def test_contiguous_phrase_outranks_disconnected_character_coincidence():
    index = BM25Index({"disconnected": "清；理；垃；圾", "phrase": "清理垃圾"})
    assert index.search("清理垃圾", 2)[0][0] == "phrase"


def test_single_han_query_still_retrieves_and_missing_query_does_not():
    index = BM25Index({"a": "山", "b": "海", "empty": ""})
    assert index.search("山", 10)[0][0] == "a"
    assert index.search("inexistent", 10) == []
    assert index.search("", 10) == []
    assert BM25Index({}).search("山", 10) == []


def test_weight_is_a_general_unigram_fallback_not_a_domain_word_list():
    full = BM25Index({"a": "甲；乙", "b": "丙"}, han_unigram_weight=1)
    weak = BM25Index({"a": "甲；乙", "b": "丙"}, han_unigram_weight=0.25)
    assert weak.search("甲", 1)[0][1] == pytest.approx(full.search("甲", 1)[0][1] * 0.25)
    # Latin tokens retain standard BM25 weight.
    full = BM25Index({"a": "word"}, han_unigram_weight=1)
    weak = BM25Index({"a": "word"}, han_unigram_weight=0.25)
    assert weak.search("word", 1) == full.search("word", 1)


def test_tie_order_is_stable_across_input_order():
    assert BM25Index({"z": "词组", "a": "词组"}).search("词组", 2) == (
        BM25Index({"a": "词组", "z": "词组"}).search("词组", 2)
    )
    assert BM25Index({"z": "词组", "a": "词组"}).search("词组", 2)[0][0] == "a"


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_top_k_is_rejected(limit):
    with pytest.raises(ValueError):
        BM25Index({"a": "text"}).search("text", limit)


@pytest.mark.parametrize(
    "params",
    [
        {"k1": 0},
        {"k1": math.nan},
        {"k1": math.inf},
        {"b": -1},
        {"b": 1.1},
        {"han_unigram_weight": 0},
        {"han_unigram_weight": 1.1},
        {"han_unigram_weight": math.nan},
    ],
)
def test_invalid_index_parameters_are_rejected(params):
    with pytest.raises(ValueError):
        BM25Index({"a": "text"}, **params)


def test_inverted_index_matches_independent_weighted_bm25_calculation():
    documents = {"a": "甲乙丙 甲乙", "b": "甲。乙 beta", "c": "beta beta 丙", "empty": ""}
    index = BM25Index(documents)
    terms = Counter(tokenize("甲乙 beta 甲乙"))
    total_query_terms = sum(terms.values())
    expected = []
    for oid, text in documents.items():
        tokens = tokenize(text)
        tf = Counter(tokens)
        score = 0.0
        for term in terms:
            if term not in tf:
                continue
            df = sum(term in tokenize(doc) for doc in documents.values())
            idf = math.log(1 + (len(documents) - df + 0.5) / (df + 0.5))
            weight = 0.25 if len(term) == 1 and "\u4e00" <= term <= "\u9fff" else 1
            norm = 1.2 * (1 - 0.75 + 0.75 * (len(tokens) or 1) / index.avgdl)
            relative_qtf = terms[term] / total_query_terms
            score += relative_qtf * weight * idf * tf[term] * 2.2 / (tf[term] + norm)
        if score:
            expected.append((oid, score))
    expected.sort(key=lambda item: (-item[1], item[0]))
    actual = index.search("甲乙 beta 甲乙", 20)
    assert [oid for oid, _ in actual] == [oid for oid, _ in expected]
    assert [score for _, score in actual] == pytest.approx([score for _, score in expected])


def test_search_uses_cached_statistics_not_document_counter_rebuild(monkeypatch):
    from casepath.adapters.retrieval import retrieval_catalog

    index = BM25Index({"a": "依法支付报酬", "b": "返还借款"})

    def forbidden(*args, **kwargs):
        pytest.fail("search rebuilt the document Counter")

    monkeypatch.setattr(retrieval_catalog, "Counter", forbidden)
    assert index.search("报酬", 2)[0][0] == "a"


def test_exact_and_contained_chunks_are_deduplicated_without_semantic_merging():
    assert _distinct_search_chunks(["  甲乙  ", "甲乙", "甲", "丙丁", ""]) == ["甲乙", "丙丁"]
    assert _distinct_search_chunks(["不得解除", "可以解除"]) == ["不得解除", "可以解除"]
    assert _distinct_search_chunks(["AB C", "A BC"]) == ["AB C", "A BC"]


def _rule():
    return RuleRecord.model_validate(
        {
            "rule_id": "rule.synthetic",
            "title": "履行义务",
            "claim_types": ["继续履行"],
            "maturity": "L1",
            "provisions": [
                {
                    "source_id": "law.test",
                    "provision_id": "law.test.1",
                    "article_no": "1",
                    "title": "测试法第一条",
                }
            ],
            "conditions": [
                {
                    "condition_id": "cond.synthetic",
                    "label": "已约定履行义务",
                    "predicate": "双方是否约定了履行时间",
                    "source_span_ids": ["span.test"],
                }
            ],
            "condition_groups": [
                {
                    "group_id": "group.synthetic",
                    "label": "基本条件",
                    "role": "CORE",
                    "operator": "ALL",
                    "member_condition_ids": ["cond.synthetic"],
                }
            ],
            "exceptions": [
                {
                    "exception_id": "exception.synthetic",
                    "label": "客观障碍",
                    "predicate": "履行因客观原因无法进行",
                    "effect": "不强制继续履行",
                    "source_span_ids": ["span.test"],
                }
            ],
            "consequences": [
                {
                    "consequence_id": "consequence.synthetic",
                    "consequence_type": "补救",
                    "description": "权利人可以请求采取补救措施",
                    "source_span_ids": ["span.test"],
                }
            ],
            "source_spans": [
                {
                    "span_id": "span.test",
                    "source_id": "law.test",
                    "start_offset": 0,
                    "end_offset": 9,
                    "quote": "双方应当依约履行。",
                }
            ],
        }
    )


def test_rule_text_covers_existing_exceptions_consequences_and_fact_questions():
    rule = _rule()
    before = rule.model_dump()
    text = _rule_search_text(rule, {"law.test.1": "双方应当依约履行。不得违反法律规定。"})
    assert "双方是否约定了履行时间" in text
    assert "履行因客观原因无法进行" in text
    assert "不强制继续履行" in text
    assert "权利人可以请求采取补救措施" in text
    assert "不得违反法律规定。" in text
    assert text.count("双方应当依约履行。") == 1
    assert rule.model_dump() == before


def test_rule_span_is_retained_when_provision_text_is_not_available():
    assert "双方应当依约履行。" in _rule_search_text(_rule(), {})


def test_rule_input_repetition_does_not_change_index_text():
    rule = _rule()
    noisy = rule.model_copy(
        update={
            "source_spans": rule.source_spans * 2,
            "provisions": rule.provisions * 2,
            "claim_types": rule.claim_types * 3,
        }
    )
    provisions = {"law.test.1": "双方应当依约履行。"}
    assert _rule_search_text(rule, provisions) == _rule_search_text(noisy, provisions)
