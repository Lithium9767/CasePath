import json
from pathlib import Path

import pytest

from casepath.adapters.evidence_explanation_planner import EvidenceExplanationPlanner
from casepath.adapters.grounded_explanation import (
    Assumption,
    Branch,
    ExplanationDraft,
    Paragraph,
    evidence_package,
)
from casepath.adapters.retrieval_catalog import load_canonical_catalog
from casepath.application.errors import InvalidComponentOutput, LanguageModelUnavailable
from casepath.contracts import (
    ComparisonBundle,
    QueryConditionState,
    QueryState,
    RetrievalBundle,
    ScoredReference,
    UserFact,
)


@pytest.fixture(scope="module")
def catalog():
    return load_canonical_catalog(Path(__file__).resolve().parents[2] / "data")


def inputs():
    state = QueryState(
        session_id="grounded",
        initial_query="还没有付款，可以解除吗？",
        user_facts=[UserFact(fact_id="user.1", text="还没有付款", source_turn=0)],
        condition_states=[
            QueryConditionState(condition_id="cond.payment_made", status="NOT_SATISFIED"),
            QueryConditionState(condition_id="cond.contract_terminated"),
        ],
    )
    bundle = RetrievalBundle(
        rule_refs=[
            ScoredReference(object_id="rule.service_contract.termination_refund.v1", score=1)
        ]
    )
    return state, bundle, ComparisonBundle()


def test_injection_has_original_evidence_and_preserves_index_identity(catalog):
    state, bundle, comparison = inputs()
    before = catalog.index_version
    package = evidence_package(catalog, state, bundle, comparison)
    assert package["rules"][0]["condition_groups"]
    assert package["rules"][0]["consequences"]
    assert any(item["quote"] for item in package["evidence"].values())
    assert package["conditions"][0]["status"] == "NOT_SATISFIED"
    assert package["facts"][0]["text"] == "还没有付款"
    assert catalog.index_version == before
    assert "expected_information_gain" not in json.dumps(package)


def test_llm_is_called_with_evidence_and_returns_dynamic_branches(catalog):
    class Model:
        def generate_structured(self, **kwargs):
            assert "不执行" in kwargs["system_prompt"]
            package = json.loads(kwargs["user_prompt"])
            rule = package["rules"][0]
            citation = rule["evidence_ids"][0]
            return ExplanationDraft(
                paragraphs=[
                    Paragraph(
                        text="你目前没有付款，费用返还与解除条件需要分别核对。",
                        evidence_ids=[citation],
                        fact_ids=["user.1"],
                    )
                ],
                branches=[
                    Branch(
                        rule_id=rule["rule_id"],
                        assumptions=[
                            Assumption(condition_id="cond.contract_terminated", status="SATISFIED")
                        ],
                        explanation=Paragraph(
                            text="假设解除已经生效，再核对是否存在待返还费用。",
                            evidence_ids=[citation],
                        ),
                    )
                ],
            )

    state, bundle, comparison = inputs()
    result = EvidenceExplanationPlanner(catalog, Model()).build(state, bundle, comparison)
    assert "你目前没有付款" in result.main_explanation
    assert "[1]" in result.main_explanation
    assert result.conditional_branches[0].condition.startswith("假设：")
    assert all(not item.verified for item in result.citations)
    assert "互信息" not in result.main_explanation


@pytest.mark.parametrize("evidence,facts", [(["invented"], []), ([], ["fake-fact"]), ([], [])])
def test_unknown_or_missing_references_fail_closed(catalog, evidence, facts):
    class Model:
        def generate_structured(self, **kwargs):
            return ExplanationDraft(
                paragraphs=[Paragraph(text="无根据的输出", evidence_ids=evidence, fact_ids=facts)]
            )

    with pytest.raises(InvalidComponentOutput):
        EvidenceExplanationPlanner(catalog, Model()).build(*inputs())


def test_foreign_branch_condition_is_rejected(catalog):
    class Model:
        def generate_structured(self, **kwargs):
            package = json.loads(kwargs["user_prompt"])
            rule = package["rules"][0]
            paragraph = Paragraph(text="条件化解释", evidence_ids=rule["evidence_ids"][:1])
            return ExplanationDraft(
                paragraphs=[paragraph],
                branches=[
                    Branch(
                        rule_id=rule["rule_id"],
                        assumptions=[Assumption(condition_id="invented", status="SATISFIED")],
                        explanation=paragraph,
                    )
                ],
            )

    with pytest.raises(InvalidComponentOutput):
        EvidenceExplanationPlanner(catalog, Model()).build(*inputs())


def test_llm_failure_is_not_hidden_by_template(catalog):
    class Model:
        def generate_structured(self, **kwargs):
            raise LanguageModelUnavailable("模型暂不可用")

    with pytest.raises(LanguageModelUnavailable):
        EvidenceExplanationPlanner(catalog, Model()).build(*inputs())


def test_case_injection_has_distinct_findings_reasoning_and_decisions(catalog):
    state, bundle, comparison = inputs()
    case_id = next(iter(catalog.case_records))
    bundle.support_case_refs = [ScoredReference(object_id=case_id, score=1)]
    package = evidence_package(catalog, state, bundle, comparison)
    case = package["cases"][0]
    assert case["role"] == "support"
    assert case["findings"] and case["reasoning_steps"] and case["decisions"]
