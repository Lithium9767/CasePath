import json
from pathlib import Path

import pytest

from casepath.adapters.explanation.evidence_explanation_planner import EvidenceExplanationPlanner
from casepath.adapters.explanation.grounded_explanation import (
    Assumption,
    Branch,
    ExplanationDraft,
    Paragraph,
    evidence_package,
)
from casepath.adapters.retrieval.retrieval_catalog import load_canonical_catalog
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
            assert "不得执行" in kwargs["system_prompt"]
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


def test_empty_citation_paragraph_is_dropped_not_500(catalog):
    class Model:
        def generate_structured(self, **kwargs):
            assert "禁止输出完全没有引用" in kwargs["system_prompt"]
            package = json.loads(kwargs["user_prompt"])
            citation = package["rules"][0]["evidence_ids"][0]
            return ExplanationDraft(
                paragraphs=[
                    Paragraph(
                        text="付款情况已经明确，仍需继续核对解除相关事实。",
                        evidence_ids=[citation],
                        fact_ids=["user.1"],
                    ),
                    Paragraph(text="现阶段还不能给出最终退款结论。", evidence_ids=[], fact_ids=[]),
                ]
            )

    result = EvidenceExplanationPlanner(catalog, Model()).build(*inputs())
    assert "付款情况已经明确" in result.main_explanation
    assert "最终退款结论" not in result.main_explanation


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
    # 新数据包含缺少查明或主文的 L1 案例，不能假定文件第一条恰好完整。
    complete = [
        case
        for case in catalog.case_records.values()
        if case.findings and case.reasoning_steps and case.decisions
    ]
    assert complete, "该测试需要至少一条具备三种证据的案例"
    case_id = min(complete, key=lambda case: len(case.model_dump_json())).case_id
    bundle.support_case_refs = [ScoredReference(object_id=case_id, score=1)]
    package = evidence_package(catalog, state, bundle, comparison)
    case = package["cases"][0]
    assert case["role"] == "support"
    assert case["findings"] and case["reasoning_steps"] and case["decisions"]


def test_missing_case_findings_are_not_invented(catalog):
    state, bundle, comparison = inputs()
    sparse = [case for case in catalog.case_records.values() if not case.findings]
    if not sparse:
        pytest.skip("当前发布没有缺少查明事实的案例")
    case_id = min(sparse, key=lambda case: len(case.model_dump_json())).case_id
    bundle.boundary_case_refs = [ScoredReference(object_id=case_id, score=1)]
    package = evidence_package(catalog, state, bundle, comparison)
    assert package["cases"][0]["findings"] == []
