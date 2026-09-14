"""Synthetic-only tests for optional, bounded case-text feedback."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace

import pytest

from casepath.adapters.p4_factory import HybridCypherP4Factory
from casepath.adapters.retrieval.local_feedback import LocalFeedbackIndex, is_fact_narrative
from casepath.adapters.retrieval.retrieval_catalog import (
    BM25Index,
    MemoryHybridSearch,
    build_catalog,
    tokenize,
)
from casepath.contracts import CaseIndexRecord, RuleRecord
from casepath.ports.embedding import EmbeddingSpec

FIRST_SENTENCE = "山林观察记录已经整理完成"
SECOND_SENTENCE = "溪流采样结果仍需继续核对"
NARRATIVE = FIRST_SENTENCE + "。" + SECOND_SENTENCE + "。"


def _documents():
    # Thirty distinct CJK bigrams occur in one seed and one non-seed each.
    # Twenty documents make their df=2 valid and ubiquitous df=20 invalid.
    documents = {}
    for number in range(5):
        terms = [
            chr(0x6000 + 12 * number + 2 * offset)
            + chr(0x6001 + 12 * number + 2 * offset)
            for offset in range(6)
        ]
        extra = " 阮沐" if number == 0 else ""
        documents[f"seed{number}"] = NARRATIVE + " 通用 " + " ".join(terms) + extra
        documents[f"pair{number}"] = "通用 " + " ".join(terms)
    for number in range(10):
        documents[f"filler{number}"] = f"通用 unrelated_{number}"
    return documents


def _catalog():
    return build_catalog(
        release_id="synthetic-feedback",
        rules=[RuleRecord(
            rule_id="rule.synthetic", title=NARRATIVE, maturity="L1",
            claim_types=["synthetic"],
            provisions=[{
                "source_id": "source.synthetic", "provision_id": "provision.synthetic",
                "article_no": "1", "title": "synthetic",
            }],
        )],
        provisions=[],
        lightweight=[
            CaseIndexRecord(case_id=key, title="synthetic", facts_text=text)
            for key, text in _documents().items()
        ],
        deep=[],
    )


@pytest.mark.parametrize("separator", ["。", "！", "？", "?", "!", "；", ";"])
def test_gate_accepts_two_distinct_long_sentence_segments(separator):
    assert is_fact_narrative(FIRST_SENTENCE + separator + SECOND_SENTENCE)


@pytest.mark.parametrize("separator", ["\n", "\r\n", " ", ",", "，", ".", ":"])
def test_gate_does_not_invent_sentence_boundaries(separator):
    assert not is_fact_narrative(FIRST_SENTENCE + separator + SECOND_SENTENCE)


@pytest.mark.parametrize("separator", ["。", "!", ";"])
def test_repeating_same_long_sentence_does_not_enable_feedback(separator):
    text = f" {FIRST_SENTENCE} {separator}\n{FIRST_SENTENCE}{separator}{FIRST_SENTENCE} "
    assert not is_fact_narrative(text)
    index = BM25Index(_documents())
    result = LocalFeedbackIndex(index).search(text, 10)
    assert result.hits == tuple(index.search(text, 10))
    assert not result.terms and not result.seed_ids


def test_gate_length_threshold_is_eight_characters_per_distinct_segment():
    assert is_fact_narrative("abcdefgh;ijklmnop")
    assert not is_fact_narrative("abcdefg;ijklmnop")
    assert not is_fact_narrative("abcdefgh;abcdefgh")
    assert not is_fact_narrative("abcdefgh;   ijklmno  ")


@pytest.mark.parametrize("query", ["山林", FIRST_SENTENCE, "主题；短句", "", "！？"])
def test_short_queries_are_identical_to_bm25_including_scores(query):
    index = BM25Index(_documents())
    result = LocalFeedbackIndex(index).search(query, 50)
    assert result.hits == tuple(index.search(query, 50))
    assert result.terms == ()
    assert result.seed_ids == ()


@pytest.mark.parametrize("query", ["", "山林", NARRATIVE])
@pytest.mark.parametrize("top_k", [0, -1, True, False, 1.5, "5", None])
def test_invalid_top_k_is_rejected_before_gate_even_for_empty_query(query, top_k):
    with pytest.raises(ValueError, match="top_k"):
        LocalFeedbackIndex(BM25Index(_documents())).search(query, top_k)


def test_empty_corpus_and_unmatched_narrative_return_no_hits_or_feedback():
    assert is_fact_narrative("nonexistent_first;nonexistent_second")
    for documents in ({}, _documents()):
        result = LocalFeedbackIndex(BM25Index(documents)).search(
            "nonexistent_first;nonexistent_second", 10
        )
        assert result.hits == result.terms == result.seed_ids == ()


def test_feedback_terms_are_bounded_source_bigrams_from_exact_initial_top_five():
    index = BM25Index(_documents())
    result = LocalFeedbackIndex(index).search(NARRATIVE, 10)
    original_seeds = tuple(oid for oid, _ in index.search(NARRATIVE, index.n)[:5])
    assert set(original_seeds) == {f"seed{number}" for number in range(5)}
    assert result.seed_ids == original_seeds
    seed_terms = set().union(*(set(index.tokens[oid]) for oid in original_seeds))
    eligible = {
        term for term in seed_terms
        if len(term) == 2
        and all("\u4e00" <= char <= "\u9fff" for char in term)
        and term not in tokenize(NARRATIVE)
        and 2 <= len(index._postings[term]) <= 0.35 * index.n
    }
    assert len(eligible) == 30
    assert len(result.terms) == 20
    assert set(result.terms) <= eligible
    assert len(set(result.terms)) == len(result.terms)
    assert "阮沐" not in result.terms  # Unique df=1 name cannot seed expansion.
    assert "通用" not in result.terms  # Ubiquitous boilerplate is excluded.
    assert not set(result.terms) & set(tokenize(NARRATIVE))


def test_frequency_filter_falls_back_exactly_when_no_eligible_feedback_exists():
    # df>=2 and df<=0.35*n cannot hold simultaneously for n=5.
    documents = {f"small{number}": NARRATIVE + " 测点" for number in range(5)}
    index = BM25Index(documents)
    result = LocalFeedbackIndex(index).search(NARRATIVE, 10)
    assert result.hits == tuple(index.search(NARRATIVE, 10))
    assert result.terms == result.seed_ids == ()


def test_feedback_scores_match_independent_normalized_weak_addition():
    index = BM25Index(_documents())
    result = LocalFeedbackIndex(index).search(NARRATIVE, 15)
    base = index.search(NARRATIVE, index.n)
    expansion = index.search(" ".join(result.terms), index.n)
    expected = {oid: score / base[0][1] for oid, score in base}
    for oid, score in expansion:
        expected[oid] = expected.get(oid, 0.0) + 0.2 * score / expansion[0][1]
    expected_ranked = sorted(expected.items(), key=lambda row: (-row[1], row[0]))[:15]
    assert [oid for oid, _ in result.hits] == [oid for oid, _ in expected_ranked]
    assert [score for _, score in result.hits] == pytest.approx(
        [score for _, score in expected_ranked]
    )
    assert all(0 < score <= 1.2 for _, score in result.hits)


def test_feedback_is_deterministic_and_does_not_mutate_index_or_source_documents():
    documents = _documents()
    documents_before = deepcopy(documents)
    index = BM25Index(documents)
    before = deepcopy(index.__dict__)
    feedback = LocalFeedbackIndex(index)
    first = feedback.search(NARRATIVE, 10)
    assert feedback.search(NARRATIVE, 10) == first
    reverse_index = BM25Index(dict(reversed(list(documents.items()))))
    assert LocalFeedbackIndex(reverse_index).search(NARRATIVE, 10) == first
    assert index.__dict__ == before
    assert documents == documents_before
    with pytest.raises(FrozenInstanceError):
        first.terms = ("改动",)


def test_feedback_top_k_only_limits_output_not_initial_seed_selection():
    feedback = LocalFeedbackIndex(BM25Index(_documents()))
    short = feedback.search(NARRATIVE, 1)
    long = feedback.search(NARRATIVE, 20)
    assert len(short.hits) == 1
    assert short.hits == long.hits[:1]
    assert short.terms == long.terms
    assert short.seed_ids == long.seed_ids
    assert len(short.seed_ids) == 5


def test_memory_feedback_is_case_only_and_default_false_preserves_legacy_results():
    catalog = _catalog()
    before = deepcopy(catalog)
    default = MemoryHybridSearch(catalog)
    disabled = MemoryHybridSearch(catalog, case_feedback=False)
    enabled = MemoryHybridSearch(catalog, case_feedback=True)
    common = {"context": catalog.context(), "top_k": 10}
    assert default.case_feedback is False
    assert default._case_feedback_index is None
    for target in ("case", "rule"):
        assert default.search(NARRATIVE, target=target, **common) == disabled.search(
            NARRATIVE, target=target, **common
        )
    assert enabled.search(NARRATIVE, target="rule", **common) == default.search(
        NARRATIVE, target="rule", **common
    )
    assert enabled.search("山林", target="case", **common) == default.search(
        "山林", target="case", **common
    )
    assert enabled.search(NARRATIVE, target="case", **common) != default.search(
        NARRATIVE, target="case", **common
    )
    assert catalog == before


@pytest.mark.parametrize("value", [0, 1, -1, "true", "false", None, [], {}, 0.0, 1.0])
def test_memory_feedback_switch_requires_a_real_boolean(value):
    with pytest.raises(ValueError, match="boolean"):
        MemoryHybridSearch(_catalog(), case_feedback=value)


@pytest.mark.parametrize("field", ["release_id", "index_version"])
def test_feedback_does_not_bypass_catalog_context_isolation(field):
    catalog = _catalog()
    context = replace(catalog.context(), **{field: "other"})
    with pytest.raises(ValueError):
        MemoryHybridSearch(catalog, case_feedback=True).search(
            NARRATIVE, target="case", context=context, top_k=10
        )


class _SyntheticEmbedding:
    spec = EmbeddingSpec(model="synthetic", revision="v1", dimensions=2)

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(tuple(texts))
        return [[1.0, 0.0] for _ in texts]


class _SyntheticVectorIndex:
    def validate(self, catalog, model):
        assert model.spec.dimensions == 2
        return {
            "case": {key: (1.0, 0.0) for key in catalog.cases},
            "rule": {key: (1.0, 0.0) for key in catalog.rules},
        }


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("vector_enabled", [False, True])
def test_factory_passes_feedback_switch_without_extra_embedding_calls(enabled, vector_enabled):
    catalog = _catalog()
    model = _SyntheticEmbedding() if vector_enabled else None
    vector_index = _SyntheticVectorIndex() if vector_enabled else None
    components = HybridCypherP4Factory(
        catalog=catalog, embedding_model=model, vector_index=vector_index,
        case_feedback=enabled,
    ).build(graph_gateway=None, language_model=None)
    search = components.workflow_dependencies.case_retriever.search
    assert search is components.workflow_dependencies.rule_retriever.search
    lexical = search.lexical if vector_enabled else search
    assert lexical.case_feedback is enabled
    capability = next(item for item in components.capabilities if item.capability == "case_retriever")
    assert ("本地低权词法反馈" in capability.reason) is enabled
    if vector_enabled:
        assert model.calls == []  # Constructing feedback cannot embed documents.
    result = search.search(NARRATIVE, target="case", context=catalog.context(), top_k=10)
    assert result
    if vector_enabled:
        # Only the existing one query embedding is used; expansion terms are not embedded.
        assert model.calls == [(NARRATIVE,)]


@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_factory_feedback_switch_is_strict_boolean(value):
    with pytest.raises(ValueError, match="boolean"):
        HybridCypherP4Factory(catalog=_catalog(), case_feedback=value)


def test_factory_defaults_to_feedback_disabled():
    components = HybridCypherP4Factory(catalog=_catalog()).build(
        graph_gateway=None, language_model=None
    )
    assert components.workflow_dependencies.case_retriever.search.case_feedback is False
