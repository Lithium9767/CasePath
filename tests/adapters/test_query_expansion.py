from concurrent.futures import ThreadPoolExecutor

import pytest

from casepath.adapters.retrieval.query_expansion import ExpandedQuerySearch, SearchTerms
from casepath.adapters.retrieval.retrieval_catalog import scored_reference
from casepath.application.errors import LanguageModelUnavailable


class Model:
    def __init__(self, terms):
        self.terms = terms
        self.calls = 0

    def generate_structured(self, **kwargs):
        self.calls += 1
        assert kwargs["stage"] == "query_expansion"
        return SearchTerms(terms=self.terms)


class Search:
    def __init__(self):
        self.calls = []

    def search(self, text, *, target, context, top_k):
        self.calls.append((text, target, top_k))
        key = "expanded" if "检索术语" in text else "original"
        return [scored_reference(key, 1, channels=[channel], components=[], reasons=[])
                for channel in ("bm25", "vector")]


def test_a_c_share_expansion_and_keep_original():
    model, backend, context = Model(["预付费争议"]), Search(), object()
    search = ExpandedQuerySearch(backend, model)
    for target in ("rule", "case"):
        refs = search.search("店关门了", target=target, context=context, top_k=3)
        assert {ref.object_id for ref in refs} == {"original", "expanded"}
        assert all("非用户事实" not in ref.object_id for ref in refs)
    assert model.calls == 1
    assert backend.calls[0][0] == backend.calls[2][0] == "店关门了"
    search.search("其他问题", target="rule", context=context, top_k=3)
    assert model.calls == 2


def test_empty_or_conclusion_expansion_uses_original_only():
    for terms in ([], ["必然胜诉", "已经解除", "x" * 25]):
        backend = Search()
        search = ExpandedQuerySearch(backend, Model(terms))
        refs = search.search("问题", target="rule", context=object(), top_k=1)
        assert len(backend.calls) == 1
        assert all(ref.object_id == "original" for ref in refs)


def test_failure_is_not_silent_fallback():
    class Broken:
        def generate_structured(self, **kwargs):
            raise LanguageModelUnavailable("offline")
    with pytest.raises(LanguageModelUnavailable):
        ExpandedQuerySearch(Search(), Broken()).search(
            "问题", target="rule", context=object(), top_k=1,
        )


def test_context_isolation():
    model = Model(["争议"])
    search = ExpandedQuerySearch(Search(), model)
    def run(_):
        context = object()
        search.search("原话", target="rule", context=context, top_k=2)
        search.search("原话", target="case", context=context, top_k=2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, range(4)))
    assert model.calls == 4
