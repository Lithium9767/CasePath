"""Conservative, evaluation-only case text; never index annotation metadata.

This strict evaluator wrapper shares the production fact-prefix extractor but
still excludes all metadata and requires a nonempty factual prefix. The view
does not modify source records or the graph and is not a legal fact classifier.
"""

from __future__ import annotations

from casepath.adapters.retrieval.case_facts import extract_case_fact_prefix
from casepath.contracts import CaseIndexRecord

CASE_TEXT_POLICY = "facts_only_conservative_prefix_v1"

def build_case_search_text(light: CaseIndexRecord) -> str:
    """Use only facts_text, without title, cause, keywords, claims or outcomes.

    Quoted pleading requests are not treated as an issued judgment. No fallback
    to labels or a case title is allowed when the factual prefix is empty.
    """
    result = extract_case_fact_prefix(light.facts_text)
    if not result:
        raise ValueError(f"no evaluation facts prefix for case {light.case_id}")
    return result
