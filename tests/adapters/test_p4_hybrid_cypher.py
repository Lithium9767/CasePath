from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date

import pytest

from casepath.adapters.conditions.condition_comparator import ConditionComparator
from casepath.adapters.conditions.semantic_answer_interpreter import SemanticAnswerInterpreter
from casepath.adapters.conditions.semantic_condition_projector import (
    ConditionLinkBatch,
    ConditionLinkDraft,
    SemanticConditionProjector,
)
from casepath.adapters.demo import build_demo_workflow
from casepath.adapters.explanation.evidence_explanation_planner import EvidenceExplanationPlanner
from casepath.adapters.p4_factory import HybridCypherP4Factory
from casepath.adapters.questioning.key_condition_policy import (
    KeyConditionQuestionPolicy,
    _compatible_rules,
)
from casepath.adapters.retrieval.graph_navigator import MemoryGraphNavigator
from casepath.adapters.retrieval.hybrid_cypher import (
    HybridCypherCaseRetriever,
    HybridCypherRuleRetriever,
    HybridCypherSettings,
)
from casepath.adapters.retrieval.retrieval_catalog import (
    MemoryHybridSearch,
    bind_context,
    build_catalog,
    current_context,
    current_round,
    scored_reference,
)
from casepath.application.errors import RetrieverUnavailable
from casepath.contracts import (
    AnswerRequest,
    CapabilityMode,
    CaseIndexRecord,
    CaseRecord,
    ClaimRecord,
    ConditionFinding,
    ConditionGroup,
    ConditionGroupOperator,
    ConditionGroupRole,
    ConditionStatus,
    CourtFinding,
    DecisionItem,
    DecisionStatus,
    LegalConsequence,
    LegalSourceRecord,
    MaturityLevel,
    ProvisionRecord,
    ProvisionRef,
    QueryConditionState,
    QueryState,
    QuestionCandidate,
    ReasoningStep,
    RuleCondition,
    RuleRecord,
    ScoreComponent,
    SourceSpan,
)
from casepath.workflow import CasePathWorkflow


def _provision(number: str, text: str) -> ProvisionRecord:
    return ProvisionRecord(
        provision_id=f"law.article_{number}",
        source_id="law.test",
        article_no=number,
        title=f"第{number}条",
        text=text,
        effective_from=date(2021, 1, 1),
        source_spans=[
            SourceSpan(
                span_id=f"span.prov.{number}",
                source_id="law.test",
                start_offset=0,
                end_offset=len(text),
                quote=text,
            )
        ],
    )


def _rule(
    rule_id: str,
    title: str,
    claims: list[str],
    conditions: list[RuleCondition],
    core: list[str],
    diagnostic: list[str] | None = None,
    core_operator: ConditionGroupOperator = ConditionGroupOperator.ALL,
) -> RuleRecord:
    groups = [
        ConditionGroup(
            group_id=f"{rule_id}.core",
            label="core",
            role=ConditionGroupRole.CORE,
            operator=core_operator,
            member_condition_ids=core,
        )
    ]
    if diagnostic:
        groups.append(
            ConditionGroup(
                group_id=f"{rule_id}.diag",
                label="diag",
                role=ConditionGroupRole.DIAGNOSTIC,
                operator=ConditionGroupOperator.ALL,
                member_condition_ids=diagnostic,
            )
        )
    return RuleRecord(
        rule_id=rule_id,
        title=title,
        claim_types=claims,
        provisions=[
            ProvisionRef(
                source_id="law.test",
                provision_id="law.article_0563",
                article_no="563",
                title="第563条",
                valid_from=date(2021, 1, 1),
            )
        ],
        conditions=conditions,
        condition_groups=groups,
        consequences=[
            LegalConsequence(
                consequence_id=f"{rule_id}.c",
                consequence_type="解除",
                description="取得解除权",
                source_span_ids=["span.563"],
            )
        ],
        maturity=MaturityLevel.L3,
        source_spans=[
            SourceSpan(
                span_id="span.563",
                source_id="law.test",
                start_offset=0,
                end_offset=4,
                quote="可以解除",
            )
        ],
    )


def _condition(condition_id: str, label: str, predicate: str) -> RuleCondition:
    return RuleCondition(
        condition_id=condition_id,
        label=label,
        predicate=predicate,
        evidence_types=["通知"],
        source_span_ids=["span.563"],
    )


def _deep_case(
    case_id: str,
    title: str,
    assessments: list[tuple[str, ConditionStatus]],
    invoked: str,
    *,
    finding_predicate: str = "经营者关闭门店并停止提供约定服务",
    span_quote: str = "关闭门店并停止",
    claim_type: str = "服务合同解除与返还",
    applied_rule_ids: list[str] | None = None,
    invoked_rule_ids: list[str] | None = None,
    cause: str = "服务合同纠纷",
) -> CaseRecord:
    findings = [
        CourtFinding(
            finding_id=f"{case_id}.f1",
            predicate=finding_predicate,
            polarity=True,
            source_span_ids=[f"{case_id}.span"],
        )
    ]
    applied = [invoked] if applied_rule_ids is None else applied_rule_ids
    invoked_ids = [invoked] if invoked_rule_ids is None else invoked_rule_ids
    reasoning = []
    if applied:
        reasoning.append(
            ReasoningStep(
                reasoning_id=f"{case_id}.r",
                applied_rule_ids=applied,
                conclusion="可以解除",
                source_span_ids=[f"{case_id}.span"],
            )
        )
    return CaseRecord(
        case_id=case_id,
        title=title,
        cause=cause,
        maturity=MaturityLevel.L1,
        claims=[
            ClaimRecord(
                claim_id=f"{case_id}.claim",
                claim_type=claim_type,
                requested_remedy="解除并返还",
                invoked_rule_ids=invoked_ids,
            )
        ],
        findings=findings,
        condition_findings=[
            ConditionFinding(
                condition_id=condition_id,
                status=status,
                finding_ids=[f"{case_id}.f1"] if status is not ConditionStatus.UNKNOWN else [],
                confidence=0.7,
                source_span_ids=[f"{case_id}.span"],
            )
            for condition_id, status in assessments
        ],
        reasoning_steps=reasoning,
        decisions=[
            DecisionItem(
                decision_id=f"{case_id}.d",
                claim_id=f"{case_id}.claim",
                status=DecisionStatus.PARTIALLY_GRANTED,
                description="部分支持",
                source_span_ids=[f"{case_id}.span"],
            )
        ],
        source_spans=[
            SourceSpan(
                span_id=f"{case_id}.span",
                source_id=f"case.{case_id}",
                start_offset=0,
                end_offset=max(1, len(span_quote)),
                quote=span_quote,
            )
        ],
    )


def fixture_catalog(*, release="rel-a"):
    exists = _condition("cond.contract_exists", "合同成立", "是否存在服务合同")
    pay = _condition("cond.payment_made", "已付款", "用户是否已付款")
    impossible = _condition("cond.performance_impossible", "履行不能", "经营者是否无法继续履行")
    alt = _condition("cond.alternative_performance", "替代履行", "是否存在替代履行")
    rules = [
        _rule(
            "rule.nonperformance.v1",
            "合同目的不能实现解除",
            ["服务合同解除与返还"],
            [exists, impossible, alt],
            ["cond.contract_exists", "cond.performance_impossible"],
            ["cond.alternative_performance"],
        ),
        _rule(
            "rule.refund.v1",
            "服务合同解除与返还",
            ["服务合同解除与返还"],
            [exists, pay, impossible, alt],
            ["cond.contract_exists", "cond.payment_made", "cond.performance_impossible"],
            ["cond.alternative_performance"],
        ),
    ]
    provisions = [_provision("0563", "当事人可以解除合同。")]
    text_case = "case.gym"
    graph_case = "case.graph-only"
    lights = [
        CaseIndexRecord(
            case_id=text_case,
            title="健身房关门退费案",
            cause="服务合同纠纷",
            keywords=["健身", "关门"],
        ),
        CaseIndexRecord(
            case_id=graph_case,
            title="土地登记更正纠纷",
            cause="物权纠纷",
            keywords=["登记", "不动产"],
        ),
        CaseIndexRecord(
            case_id="case.cite-only",
            title="商标异议复审行政纠纷",
            cause="商标权纠纷",
            keywords=["商标", "异议"],
            related_provision_ids=["law.article_0563"],
        ),
        CaseIndexRecord(
            case_id="case.unknown-only",
            title="咨询服务备案审查",
            cause="行政备案",
            keywords=["备案"],
        ),
        CaseIndexRecord(
            case_id="case.text-only",
            title="健身房会员卡转让咨询",
            cause="服务合同纠纷",
            keywords=["健身", "关门"],
        ),
    ]
    deep = [
        _deep_case(
            text_case,
            "健身房关门退费案",
            [
                ("cond.contract_exists", ConditionStatus.SATISFIED),
                ("cond.payment_made", ConditionStatus.SATISFIED),
                ("cond.performance_impossible", ConditionStatus.SATISFIED),
                ("cond.alternative_performance", ConditionStatus.NOT_SATISFIED),
            ],
            "rule.refund.v1",
        ),
        _deep_case(
            graph_case,
            "土地登记更正纠纷",
            [
                ("cond.performance_impossible", ConditionStatus.SATISFIED),
                ("cond.alternative_performance", ConditionStatus.SATISFIED),
            ],
            "rule.nonperformance.v1",
            finding_predicate="登记簿记载与实际权属不符",
            span_quote="登记簿记载不符",
            claim_type="物权确认",
            applied_rule_ids=[],
            invoked_rule_ids=[],
            cause="物权纠纷",
        ),
        _deep_case(
            "case.unknown-only",
            "咨询服务备案审查",
            [
                ("cond.contract_exists", ConditionStatus.UNKNOWN),
                ("cond.payment_made", ConditionStatus.UNKNOWN),
                ("cond.performance_impossible", ConditionStatus.UNKNOWN),
            ],
            "rule.refund.v1",
            finding_predicate="当事人未就履行事实作出明确认定",
            span_quote="未作明确认定",
            cause="行政备案",
        ),
    ]
    return build_catalog(
        release_id=release,
        rules=rules,
        provisions=provisions,
        lightweight=lights,
        deep=deep,
    )


def _fixture_projection(release="rel-a"):
    catalog = fixture_catalog(release=release)
    source = LegalSourceRecord(source_id="law.test", title="测试法", source_type="LAW")
    exists = _condition("cond.contract_exists", "合同成立", "是否存在服务合同")
    pay = _condition("cond.payment_made", "已付款", "用户是否已付款")
    impossible = _condition("cond.performance_impossible", "履行不能", "经营者是否无法继续履行")
    alt = _condition("cond.alternative_performance", "替代履行", "是否存在替代履行")
    rules = [
        _rule(
            "rule.nonperformance.v1",
            "合同目的不能实现解除",
            ["服务合同解除与返还"],
            [exists, impossible, alt],
            ["cond.contract_exists", "cond.performance_impossible"],
            ["cond.alternative_performance"],
        ),
        _rule(
            "rule.refund.v1",
            "服务合同解除与返还",
            ["服务合同解除与返还"],
            [exists, pay, impossible, alt],
            ["cond.contract_exists", "cond.payment_made", "cond.performance_impossible"],
            ["cond.alternative_performance"],
        ),
    ]
    provisions = [_provision("0563", "当事人可以解除合同。")]
    lights = [
        CaseIndexRecord(
            case_id="case.gym",
            title="健身房关门退费案",
            cause="服务合同纠纷",
            keywords=["健身", "关门"],
        ),
        CaseIndexRecord(
            case_id="case.graph-only",
            title="土地登记更正纠纷",
            cause="物权纠纷",
            keywords=["登记", "不动产"],
        ),
        CaseIndexRecord(
            case_id="case.cite-only",
            title="商标异议复审行政纠纷",
            cause="商标权纠纷",
            keywords=["商标", "异议"],
            related_provision_ids=["law.article_0563"],
        ),
        CaseIndexRecord(
            case_id="case.unknown-only",
            title="咨询服务备案审查",
            cause="行政备案",
            keywords=["备案"],
        ),
        CaseIndexRecord(
            case_id="case.text-only",
            title="健身房会员卡转让咨询",
            cause="服务合同纠纷",
            keywords=["健身", "关门"],
        ),
    ]
    deep = [
        _deep_case(
            "case.gym",
            "健身房关门退费案",
            [
                ("cond.contract_exists", ConditionStatus.SATISFIED),
                ("cond.payment_made", ConditionStatus.SATISFIED),
                ("cond.performance_impossible", ConditionStatus.SATISFIED),
                ("cond.alternative_performance", ConditionStatus.NOT_SATISFIED),
            ],
            "rule.refund.v1",
        ),
        _deep_case(
            "case.graph-only",
            "土地登记更正纠纷",
            [
                ("cond.performance_impossible", ConditionStatus.SATISFIED),
                ("cond.alternative_performance", ConditionStatus.SATISFIED),
            ],
            "rule.nonperformance.v1",
            finding_predicate="登记簿记载与实际权属不符",
            span_quote="登记簿记载不符",
            claim_type="物权确认",
            applied_rule_ids=[],
            invoked_rule_ids=[],
            cause="物权纠纷",
        ),
        _deep_case(
            "case.unknown-only",
            "咨询服务备案审查",
            [
                ("cond.contract_exists", ConditionStatus.UNKNOWN),
                ("cond.payment_made", ConditionStatus.UNKNOWN),
                ("cond.performance_impossible", ConditionStatus.UNKNOWN),
            ],
            "rule.refund.v1",
            finding_predicate="当事人未就履行事实作出明确认定",
            span_quote="未作明确认定",
            cause="行政备案",
        ),
    ]
    from casepath.ingestion.graph.projection import project_records

    return catalog, project_records([source], provisions, rules, [], lights, deep, input_hashes={})


def components(catalog):
    search = MemoryHybridSearch(catalog)
    nav = MemoryGraphNavigator(catalog)
    return {
        "rules": HybridCypherRuleRetriever(nav, search, catalog=catalog, navigator=nav),
        "cases": HybridCypherCaseRetriever(nav, search, catalog=catalog, navigator=nav),
        "project": SemanticConditionProjector(catalog),
        "compare": ConditionComparator(catalog),
        "ask": KeyConditionQuestionPolicy(catalog),
        "explain": EvidenceExplanationPlanner(catalog),
        "interpret": SemanticAnswerInterpreter(),
        "search": search,
        "nav": nav,
    }


def gym_state(session_id="s1") -> QueryState:
    return QueryState(
        session_id=session_id, initial_query="我在健身房充了5000元，店关门了，还有余额。"
    )


@pytest.mark.parametrize("bad", [False, True])
def test_dynamic_question_without_card_and_answer_is_raw(bad):
    from casepath.adapters.questioning.key_condition_policy import QuestionDraft
    from casepath.application.errors import QuestionGenerationFailed
    from casepath.contracts import ComparisonBundle, RetrievalBundle

    key = "cond.inheritance.will"
    catalog = build_catalog(
        release_id="question-test",
        rules=[_rule("rule.will", "遗嘱相关信息", ["民事咨询"],
                     [_condition(key, "遗嘱", "是否存在书面遗嘱")], [key])],
        provisions=[_provision("0563", "测试原文")], lightweight=[], deep=[],
    )
    class Model:
        calls = 0
        def generate_structured(self, **kwargs):
            self.calls += 1
            if kwargs["stage"] == "missing_facts":
                from casepath.adapters.questioning.missing_facts import (
                    MissingFactDraft,
                    MissingFactIdentifyBatch,
                )
                return MissingFactIdentifyBatch(items=[MissingFactDraft(
                    condition_ids=[key], description="是否找到书面遗嘱", status="MISSING",
                    why_needed="核实文件",
                )])
            assert kwargs["stage"] == "question"
            return QuestionDraft(condition_id="invalid" if bad else key, askable=True,
                                 question="家里有没有找到书面遗嘱？", reason="核实文件")
    model = Model()
    policy = KeyConditionQuestionPolicy(catalog, model)
    state = QueryState(session_id="question", initial_query="想了解遗产问题",
                       condition_states=[QueryConditionState(condition_id=key)])
    bundle = RetrievalBundle(rule_refs=[scored_reference("rule.will", 1, channels=[],
                                                        components=[], reasons=[])])
    before = state.model_dump()
    if bad:
        with pytest.raises(QuestionGenerationFailed):
            policy.select(state, bundle, ComparisonBundle())
        assert model.calls == 3
        return
    question = policy.select(state, bundle, ComparisonBundle())
    assert question.condition_id == key
    assert question.why_asked == "核实文件"
    assert question.options == ["不清楚", "暂不回答"]
    assert state.model_dump() == before
    request = AnswerRequest(question_id=question.question_id, condition_id=key,
                            answer="有")
    result = SemanticAnswerInterpreter().interpret(state, question, request)
    assert not result.condition_updates
    assert result.new_facts[0].text == "有"


def test_same_query_is_stable_and_channels_are_traced():
    catalog = fixture_catalog()
    parts = components(catalog)
    state = gym_state()
    first = parts["rules"].retrieve(state)
    second = parts["rules"].retrieve(state)
    assert [item.object_id for item in first] == [item.object_id for item in second]
    assert len({item.object_id for item in first}) == len(first)
    assert any(item.retrieval_channels for item in first)
    assert all(item.score_components for item in first)


@pytest.mark.parametrize("name", [
    "case_recall_top_k", "case_rerank_pool_size", "case_role_top_k",
])
def test_case_budgets_must_be_positive(name):
    with pytest.raises(ValueError, match="positive"):
        HybridCypherSettings(**{name: 0})


@pytest.mark.parametrize("role", ["support", "limiting", "boundary", None])
def test_case_budgets_are_independent_and_do_not_fill_roles(monkeypatch, role):
    """用角色替身隔离预算逻辑；真实角色资格仍由原有回归测试覆盖。"""
    catalog = fixture_catalog()
    parts = components(catalog)
    rules = parts["rules"].retrieve(gym_state())
    calls = []

    class Search:
        def search(self, text, *, target, context, top_k):
            calls.append((target, top_k))
            return [scored_reference(key, 1.0, channels=["bm25"], components=[], reasons=[])
                    for key in sorted(catalog.cases)[:top_k]]

    monkeypatch.setattr(
        "casepath.adapters.retrieval.hybrid_cypher._assign_role",
        lambda *args: (role, [], [], None if role else "INSUFFICIENT_EVIDENCE"),
    )
    settings = HybridCypherSettings(case_recall_top_k=7, case_rerank_pool_size=2,
                                   case_role_top_k=1)
    result = HybridCypherCaseRetriever(
        parts["nav"], Search(), settings, catalog=catalog, navigator=parts["nav"],
    ).retrieve(gym_state(), rules)
    assert calls == [("case", 7)]
    assert len(current_round().fused_case_ids) == 2
    groups = {"support": result.support_case_refs, "limiting": result.limiting_case_refs,
              "boundary": result.boundary_case_refs}
    for name, refs in groups.items():
        assert len(refs) == (1 if name == role else 0)
    selected = [ref for refs in groups.values() for ref in refs]
    assert set(result.cited_span_ids) == {span for ref in selected for span in ref.source_span_ids}


def test_default_case_budgets():
    settings = HybridCypherSettings()
    assert (settings.case_recall_top_k, settings.case_rerank_pool_size,
            settings.case_role_top_k) == (30, 20, 3)


def test_role_selection_precedes_global_truncation(monkeypatch):
    """低检索分的合格对照案例不能被同类案例提前挤掉。"""
    catalog = fixture_catalog()
    parts = components(catalog)
    rules = parts["rules"].retrieve(gym_state())
    ids = sorted(catalog.cases)
    contrast = ids[-1]

    class Search:
        def search(self, *args, **kwargs):
            return [scored_reference(key, 1.0, channels=["bm25"], components=[], reasons=[])
                    for key in ids]

    monkeypatch.setattr(
        "casepath.adapters.retrieval.hybrid_cypher._assign_role",
        lambda case, *args: (
            "limiting" if case.case_id == contrast else "support",
            [],
            [],
            None,
        ),
    )
    settings = HybridCypherSettings(case_rerank_pool_size=2, case_role_top_k=1,
                                   graph_weight=0, cites_weight=0)
    result = HybridCypherCaseRetriever(
        parts["nav"], Search(), settings, catalog=catalog, navigator=parts["nav"],
    ).retrieve(gym_state(), rules)
    assert [ref.object_id for ref in result.limiting_case_refs] == [contrast]
    assert len(result.support_case_refs) == 1
    assert contrast in current_round().fused_case_ids
    assert all(component.name != "compatibility" for ref in result.limiting_case_refs
               for component in ref.score_components)


def test_graph_only_case_enters_pool():
    catalog = fixture_catalog()
    parts = components(catalog)
    real_search = parts["search"]

    class MissGraphSearch:
        def search(self, text, *, target, context, top_k):
            hits = real_search.search(text, target=target, context=context, top_k=top_k)
            return [item for item in hits if item.object_id != "case.graph-only"]

    parts["cases"].search = MissGraphSearch()
    state = gym_state()
    rules = parts["rules"].retrieve(state)
    projected = parts["project"].project(state, rules)
    parts["cases"].retrieve(projected, rules)
    evidence = current_round()
    assert evidence is not None
    assert "case.graph-only" in evidence.fused_case_ids
    assert "case.graph-only" in evidence.graph_only_ids
    assert "bm25" not in (evidence.channels_by_case or {}).get("case.graph-only", ())
    assert "graph" in (evidence.channels_by_case or {}).get("case.graph-only", ())


def test_release_context_is_request_scoped():
    first = fixture_catalog(release="rel-a")
    second = fixture_catalog(release="rel-b")
    search = MemoryHybridSearch(first)
    with pytest.raises(ValueError, match="isolated"):
        search.search("健身房", target="rule", context=second.context(), top_k=3)
    parts = components(first)
    parts["rules"].retrieve(gym_state("a"))
    assert current_context().release_id == "rel-a"

    def other():
        components(second)["rules"].retrieve(gym_state("b"))
        return current_context().release_id

    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(other).result() == "rel-b"


def test_known_conditions_change_ranking_and_unknown_does_not_negate():
    catalog = fixture_catalog()
    parts = components(catalog)
    rules = parts["rules"].retrieve(gym_state())
    bind_context(catalog.context())
    satisfied = QueryState(
        session_id="rank",
        initial_query="健身房关门",
        condition_states=[
            QueryConditionState(
                condition_id="cond.performance_impossible", status=ConditionStatus.SATISFIED
            ),
            QueryConditionState(
                condition_id="cond.alternative_performance", status=ConditionStatus.UNKNOWN
            ),
        ],
    )
    unknown = satisfied.model_copy(
        update={
            "condition_states": [
                QueryConditionState(
                    condition_id="cond.performance_impossible", status=ConditionStatus.UNKNOWN
                ),
                QueryConditionState(
                    condition_id="cond.alternative_performance", status=ConditionStatus.UNKNOWN
                ),
            ]
        }
    )
    flipped = satisfied.model_copy(
        update={
            "condition_states": [
                QueryConditionState(
                    condition_id="cond.performance_impossible",
                    status=ConditionStatus.NOT_SATISFIED,
                ),
                QueryConditionState(
                    condition_id="cond.alternative_performance", status=ConditionStatus.UNKNOWN
                ),
            ]
        }
    )
    high = parts["cases"].retrieve(satisfied, rules)
    mid = parts["cases"].retrieve(unknown, rules)
    low = parts["cases"].retrieve(flipped, rules)

    def score(bundle, oid):
        for item in (
            *bundle.support_case_refs,
            *bundle.limiting_case_refs,
            *bundle.boundary_case_refs,
        ):
            if item.object_id == oid:
                return item.score
        return None

    # 条件改变角色和比较信息，不再直接扣减公共检索分。
    assert score(high, "case.gym") == score(low, "case.gym")
    def differences(bundle):
        refs = [*bundle.support_case_refs, *bundle.limiting_case_refs, *bundle.boundary_case_refs]
        return next(c.value for ref in refs if ref.object_id == "case.gym"
                    for c in ref.score_components if c.name == "different_conditions")

    assert differences(high) != differences(low)
    assert score(mid, "case.gym") is not None


def test_diagnostic_unsatisfied_does_not_drop_core_rule():
    catalog = fixture_catalog()
    parts = components(catalog)
    rules = parts["rules"].retrieve(gym_state())
    assert any(item.object_id == "rule.nonperformance.v1" for item in rules)
    bind_context(catalog.context())
    state = QueryState(
        session_id="diag",
        initial_query="健身房关门但可以转店",
        condition_states=[
            QueryConditionState(
                condition_id="cond.contract_exists", status=ConditionStatus.SATISFIED
            ),
            QueryConditionState(
                condition_id="cond.performance_impossible", status=ConditionStatus.SATISFIED
            ),
            QueryConditionState(
                condition_id="cond.alternative_performance", status=ConditionStatus.NOT_SATISFIED
            ),
        ],
    )
    bundle = parts["cases"].retrieve(state, rules)
    ids = {item.object_id for item in bundle.rule_refs}
    assert "rule.nonperformance.v1" in ids
    assert any(
        item.object_id == "case.gym"
        for item in (
            *bundle.support_case_refs,
            *bundle.limiting_case_refs,
            *bundle.boundary_case_refs,
        )
    )


def test_repeat_answer_and_invalid_ids_are_rejected():
    interpreter = SemanticAnswerInterpreter()
    pending = QuestionCandidate(
        question_id="question.cond.performance_impossible.1",
        condition_id="cond.performance_impossible",
        question="是否无法继续履行？",
        why_asked="测试",
        options=["已经无法继续履行", "仍能继续履行", "不清楚"],
        utility=0.5,
    )
    state = QueryState(
        session_id="ans",
        initial_query="健身房关门",
        condition_states=[QueryConditionState(condition_id="cond.performance_impossible")],
    )
    request = AnswerRequest(
        question_id=pending.question_id,
        condition_id=pending.condition_id,
        answer="所有门店都已经永久关闭了",
        selected_option="已经无法继续履行",
    )
    first = interpreter.interpret(state, pending, request)
    merged = state.model_copy(
        update={
            "user_facts": first.new_facts,
            "condition_states": first.condition_updates,
        }
    )
    second = interpreter.interpret(merged, pending, request)
    assert first.new_facts
    assert second.new_facts == []
    with pytest.raises(ValueError, match="未登记"):
        interpreter.interpret(
            state,
            pending.model_copy(update={"condition_id": "cond.invented"}),
            request.model_copy(update={"condition_id": "cond.invented"}),
        )


def test_roles_need_condition_and_span_evidence():
    catalog = fixture_catalog()
    parts = components(catalog)
    state = parts["project"].project(gym_state(), parts["rules"].retrieve(gym_state()))
    bundle = parts["cases"].retrieve(state, parts["rules"].retrieve(gym_state()))
    for item in (*bundle.support_case_refs, *bundle.limiting_case_refs, *bundle.boundary_case_refs):
        assert item.source_span_ids
        assert item.reasons


def test_comparison_is_reused_and_selection_does_not_mutate():
    catalog = fixture_catalog()
    parts = components(catalog)
    workflow = CasePathWorkflow(
        __import__("casepath.workflow", fromlist=["WorkflowDependencies"]).WorkflowDependencies(
            rule_retriever=parts["rules"],
            condition_projector=parts["project"],
            case_retriever=parts["cases"],
            case_comparator=parts["compare"],
            question_policy=parts["ask"],
            explanation_planner=parts["explain"],
        )
    )
    snapshot = workflow.run(gym_state())
    before = [item.model_dump() for item in snapshot.query_state.condition_states]
    question = parts["ask"].select(
        snapshot.query_state, snapshot.retrieval_bundle, snapshot.comparison_bundle
    )
    after = [item.model_dump() for item in snapshot.query_state.condition_states]
    assert before == after
    assert snapshot.comparison_bundle is not None
    if question is not None:
        assert any(item.name == "case_disagreement" for item in question.score_components)
    assert all(not item.verified for item in snapshot.explanation_plan.citations)


def test_empty_query_does_not_return_empty_success():
    catalog = fixture_catalog()
    parts = components(catalog)

    class DeadSearch:
        def search(self, text, *, target, context, top_k):
            raise RetrieverUnavailable("index down")

    parts["rules"].search = DeadSearch()
    with pytest.raises(RetrieverUnavailable):
        parts["rules"].retrieve(gym_state())


def test_factory_builds_without_replacing_demo():
    catalog = fixture_catalog()
    built = HybridCypherP4Factory(catalog=catalog).build(graph_gateway=None, language_model=None)
    assert built.answer_interpreter is not None
    by_name = {item.capability: item for item in built.capabilities}
    assert by_name["vector_search"].available is False
    assert by_name["vector_search"].degraded is True
    assert by_name["rule_retriever"].degraded is True
    assert by_name["answer_interpreter"].available is True
    demo = build_demo_workflow()
    assert demo.dependencies.rule_retriever.__class__.__name__ == "DemoRuleRetriever"


def test_end_to_end_workflow_on_fixture():
    catalog = fixture_catalog()
    parts = components(catalog)
    from casepath.workflow import WorkflowDependencies

    snapshot = CasePathWorkflow(
        WorkflowDependencies(
            rule_retriever=parts["rules"],
            condition_projector=parts["project"],
            case_retriever=parts["cases"],
            case_comparator=parts["compare"],
            question_policy=parts["ask"],
            explanation_planner=parts["explain"],
        )
    ).run(gym_state())
    assert snapshot.retrieval_bundle.rule_refs
    assert snapshot.comparison_bundle is not None
    assert snapshot.explanation_plan.unresolved_condition_ids or snapshot.next_question
    assert "RETRIEVE_RULES" in snapshot.trace


def _pending(condition_id: str, question: str, options: list[str]) -> QuestionCandidate:
    return QuestionCandidate(
        question_id=f"question.{condition_id}.1",
        condition_id=condition_id,
        question=question,
        why_asked="测试",
        options=options,
        utility=0.5,
    )


def _answer_state(condition_id: str, query: str = "健身房关门") -> QueryState:
    return QueryState(
        session_id="ans",
        initial_query=query,
        condition_states=[QueryConditionState(condition_id=condition_id)],
    )


def test_answer_interpreter_negation_and_option_mapping():
    interpreter = SemanticAnswerInterpreter()
    payment = _pending(
        "cond.payment_made", "你是否已经支付了约定的服务费用？", ["已经支付", "没有支付", "不清楚"]
    )
    balance = _pending(
        "cond.unperformed_balance",
        "是否还有尚未提供的服务或未消费余额？",
        ["还有未消费部分", "已经全部消费", "不清楚"],
    )
    paid = interpreter.interpret(
        _answer_state("cond.payment_made"),
        payment,
        AnswerRequest(
            question_id=payment.question_id,
            condition_id="cond.payment_made",
            answer="没有支付",
        ),
    )
    bare = interpreter.interpret(
        _answer_state("cond.payment_made"),
        payment,
        AnswerRequest(
            question_id=payment.question_id, condition_id="cond.payment_made", answer="没有"
        ),
    )
    consumed = interpreter.interpret(
        _answer_state("cond.unperformed_balance"),
        balance,
        AnswerRequest(
            question_id=balance.question_id,
            condition_id="cond.unperformed_balance",
            answer="已经全部消费",
            selected_option="已经全部消费",
        ),
    )
    assert paid.condition_updates[0].status is ConditionStatus.NOT_SATISFIED
    assert bare.condition_updates[0].status is ConditionStatus.NOT_SATISFIED
    assert consumed.condition_updates[0].status is ConditionStatus.NOT_SATISFIED


def _mapping_catalog():
    exists = _condition("cond.contract_exists", "合同成立", "是否存在服务合同")
    pay = _condition("cond.payment_made", "已付款", "用户是否已付款")
    balance = _condition("cond.unperformed_balance", "未消费余额", "是否还有未消费余额")
    terminated = _condition("cond.contract_terminated", "合同已解除", "合同是否已经解除")
    return build_catalog(
        release_id="map",
        rules=[
            _rule(
                "rule.map.v1",
                "映射测试规则",
                ["服务合同解除与返还"],
                [exists, pay, balance, terminated],
                [
                    "cond.contract_exists",
                    "cond.payment_made",
                    "cond.unperformed_balance",
                    "cond.contract_terminated",
                ],
            )
        ],
        provisions=[_provision("0563", "当事人可以解除合同。")],
        lightweight=[],
        deep=[],
    )


def _status_of(state: QueryState, condition_id: str) -> ConditionStatus:
    return next(item.status for item in state.condition_states if item.condition_id == condition_id)


def _mapping_refs():
    # 条件映射测试显式提供规则，不再依赖空召回时扫描全库的旧行为。
    from casepath.contracts import ScoredReference
    return [ScoredReference(object_id="rule.map.v1", score=1.0)]


def test_initial_projection_rejects_intent_negation_and_overclaim():
    projector = SemanticConditionProjector(_mapping_catalog())
    balance = projector.project(
        QueryState(session_id="p", initial_query="还有未消费余额"),
        _mapping_refs(),
    )
    intent = projector.project(
        QueryState(session_id="p", initial_query="我想解除合同"),
        _mapping_refs(),
    )
    unpaid = projector.project(
        QueryState(session_id="p", initial_query="费用100元，还没付款"),
        _mapping_refs(),
    )
    assert _status_of(balance, "cond.unperformed_balance") is ConditionStatus.SATISFIED
    assert _status_of(intent, "cond.contract_terminated") is ConditionStatus.UNKNOWN
    assert _status_of(unpaid, "cond.payment_made") is ConditionStatus.NOT_SATISFIED


@pytest.mark.parametrize(
    "query",
    [
        "如果已经付款，可以退吗",
        "我打算已经付款后再申请",
        "并非已经付款",
        "已经付款。没有付款",
        "假如已经付款，那么还有未消费余额",
    ],
)
def test_hypothetical_or_ambiguous_payment_is_unknown(query):
    state = SemanticConditionProjector(_mapping_catalog()).project(
        QueryState(session_id="guard", initial_query=query), _mapping_refs()
    )
    assert _status_of(state, "cond.payment_made") is ConditionStatus.UNKNOWN


def test_hypothetical_sentence_does_not_hide_separate_fact():
    state = SemanticConditionProjector(_mapping_catalog()).project(
        QueryState(session_id="guard", initial_query="如果解除合同会怎样？已经付款。"), _mapping_refs()
    )
    assert _status_of(state, "cond.payment_made") is ConditionStatus.SATISFIED


@pytest.mark.parametrize("quote,fact_id", [("", None), ("   ", None), ("咨询", "missing")])
def test_llm_cannot_attach_empty_or_wrong_fact_evidence(quote, fact_id):
    class Model:
        def generate_structured(self, **kwargs):
            return ConditionLinkBatch(
                links=[
                    ConditionLinkDraft(
                        fact_id=fact_id,
                        condition_id="cond.payment_made",
                        relation="SUPPORTS",
                        quoted_span=quote,
                        rationale="测试无效引句",
                        raw_confidence=0.9,
                    )
                ]
            )

    # 非法引句丢弃该条映射，不打崩请求，也不据此写成付款已支持。
    state = SemanticConditionProjector(_mapping_catalog(), Model()).project(
        QueryState(session_id="guard", initial_query="咨询一下，已经付款"), _mapping_refs()
    )
    assert _status_of(state, "cond.payment_made") is not ConditionStatus.SATISFIED


def test_live_projection_does_not_run_lexical_baseline(monkeypatch):
    """模型省略付款映射时，不能残留词法的肯定判断。"""
    def forbidden(*args):
        raise AssertionError("Live must not call lexical mapping")

    monkeypatch.setattr(
        "casepath.adapters.conditions.semantic_condition_projector._lexical_candidates",
        forbidden,
    )

    class Model:
        def generate_structured(self, **kwargs):
            return ConditionLinkBatch(links=[])

    state = SemanticConditionProjector(_mapping_catalog(), Model()).project(
        QueryState(session_id="llm-only", initial_query="已经付款"), _mapping_refs()
    )
    assert _status_of(state, "cond.payment_made") is ConditionStatus.UNKNOWN


@pytest.mark.parametrize(
    "fact_id,expected",
    [
        (None, ConditionStatus.UNKNOWN),
        ("f2", ConditionStatus.SATISFIED),
    ],
)
def test_llm_repeated_quote_requires_explicit_fact_id(fact_id, expected):
    from casepath.application.runtime_meta import current_runtime_meta
    from casepath.contracts import UserFact

    class Model:
        def generate_structured(self, **kwargs):
            return ConditionLinkBatch(
                links=[
                    ConditionLinkDraft(
                        fact_id=fact_id,
                        condition_id="cond.payment_made",
                        relation="SUPPORTS",
                        quoted_span="交款",
                        rationale="测试事实定位",
                        raw_confidence=0.9,
                    )
                ]
            )

    source = QueryState(
            session_id="guard",
            initial_query="交款",
            user_facts=[
                UserFact(fact_id="f1", text="交款", source_turn=0),
                UserFact(fact_id="f2", text="交款", source_turn=0),
            ],
        )
    projector = SemanticConditionProjector(_mapping_catalog(), Model())
    if fact_id is None:
        result = projector.project(source, _mapping_refs())
        assert _status_of(result, "cond.payment_made") is ConditionStatus.UNKNOWN
        assert not any(
            item.condition_id == "cond.payment_made"
            for item in current_runtime_meta().projection_ledger
        )
        return
    state = projector.project(source, _mapping_refs())
    payment = next(
        item for item in state.condition_states if item.condition_id == "cond.payment_made"
    )
    assert payment.status is expected
    if fact_id:
        assert payment.evidence[0].fact_id == fact_id


def test_roles_and_ranking_use_group_truth():
    from casepath.adapters.retrieval.hybrid_cypher import _assign_role, _compare_conditions
    from casepath.adapters.retrieval.retrieval_catalog import ConditionGroupSpec

    catalog = fixture_catalog()
    rule_id = "rule.refund.v1"
    rule = catalog.rules[rule_id]
    case = catalog.cases["case.gym"]
    known = {
        "cond.contract_exists": ConditionStatus.SATISFIED,
        "cond.payment_made": ConditionStatus.NOT_SATISFIED,
        "cond.performance_impossible": ConditionStatus.SATISFIED,
    }
    claims = {"服务合同解除与返还"}
    catalog.rules[rule_id] = replace(
        rule, groups=(ConditionGroupSpec("any", "CORE", "ANY", rule.core_ids),)
    )
    assert _assign_role(case, known, {rule_id}, catalog, claims)[0] == "support"
    assert _compare_conditions(case, known, catalog, {rule_id}).core_agree == 1
    catalog.rules[rule_id] = replace(
        rule, groups=(ConditionGroupSpec("all", "CORE", "ALL", rule.core_ids),)
    )
    assert _assign_role(case, known, {rule_id}, catalog, claims)[0] == "limiting"
    assert _compare_conditions(case, known, catalog, {rule_id}).core_conflict == 1
    catalog.rules[rule_id] = replace(
        rule, groups=tuple(ConditionGroupSpec(key, "CORE", "ALL", (key,)) for key in rule.core_ids)
    )
    assert _assign_role(case, known, {rule_id}, catalog, claims)[0] == "boundary"


def test_llm_correction_is_not_a_user_fact_conflict():
    class FlipLLM:
        def generate_structured(
            self, *, system_prompt, user_prompt, response_model, timeout_seconds=None, stage='default'
        ):
            return ConditionLinkBatch(
                links=[
                    ConditionLinkDraft(
                        condition_id="cond.payment_made",
                        relation="OPPOSES",
                        quoted_span="充了",
                        rationale="测试覆盖词法候选",
                        raw_confidence=0.9,
                    )
                ]
            )

    projector = SemanticConditionProjector(_mapping_catalog(), FlipLLM())
    state = projector.project(QueryState(session_id="llm", initial_query="我充了5000元"), _mapping_refs())
    payment = next(
        item for item in state.condition_states if item.condition_id == "cond.payment_made"
    )
    assert payment.status is ConditionStatus.NOT_SATISFIED
    assert payment.evidence[0].relation == "OPPOSES"


def test_vector_only_hits_and_version_guard(tmp_path):
    from casepath.adapters.retrieval.vector_search import VectorHybridSearch, VectorIndex
    from casepath.ports.embedding import EmbeddingSpec

    catalog = fixture_catalog()
    wanted = catalog.rules["rule.refund.v1"].search_text

    class Model:
        spec = EmbeddingSpec("fake-semantic", "test-v1", 2)

        def embed(self, texts):
            return [[1.0, 0.0] if text in {wanted, "zzzzzz"} else [0.0, 1.0] for text in texts]

    model = Model()
    index = VectorIndex.build(catalog, model, batch_size=1)
    path = tmp_path / "vectors.json"
    index.save(path)
    search = VectorHybridSearch(catalog, model, VectorIndex.load(path))
    refs = search.search("zzzzzz", target="rule", context=catalog.context(), top_k=5)
    assert [(item.object_id, item.retrieval_channels) for item in refs] == [
        ("rule.refund.v1", ["vector"])
    ]
    retriever = HybridCypherRuleRetriever(MemoryGraphNavigator(catalog), search, catalog=catalog)
    output = retriever.retrieve(QueryState(session_id="v", initial_query="zzzzzz"))
    assert any(
        item.object_id == "rule.refund.v1" and "vector" in item.retrieval_channels
        for item in output
    )
    model.spec = EmbeddingSpec("fake-semantic", "test-v2", 2)
    with pytest.raises(ValueError, match="版本"):
        VectorHybridSearch(catalog, model, index)


def test_vector_rejects_missing_records_and_bad_dimensions():
    from casepath.adapters.retrieval.vector_search import VectorHybridSearch, VectorIndex
    from casepath.ports.embedding import EmbeddingSpec

    class Model:
        spec = EmbeddingSpec("fake", "v1", 2)

        def embed(self, texts):
            return [[1, 0] for _ in texts]

    catalog = fixture_catalog()
    index = VectorIndex.build(catalog, Model())
    index.payload["records"].pop()
    with pytest.raises(ValueError, match="覆盖"):
        VectorHybridSearch(catalog, Model(), index)
    model = Model()
    model.embed = lambda texts: [[1] for _ in texts]
    with pytest.raises(ValueError, match="维度"):
        VectorIndex.build(catalog, model)


def test_new_fact_updates_other_condition_and_preserves_history():
    from casepath.contracts import ConditionEvidence, DialogueTurn, UserFact

    state = QueryState(
        session_id="history",
        initial_query="咨询",
        user_facts=[
            UserFact(fact_id="initial", text="咨询", source_turn=0),
            UserFact(fact_id="old", text="已经付款", source_turn=1),
            UserFact(fact_id="new", text="没有付款。还有余额。", source_turn=2),
        ],
        condition_states=[
            QueryConditionState(
                condition_id="cond.payment_made",
                status=ConditionStatus.SATISFIED,
                supporting_fact_ids=["old"],
                evidence=[
                    ConditionEvidence(
                        fact_id="old", relation="SUPPORTS", confidence=0.7, reason="历史事实"
                    )
                ],
                last_updated_turn=1,
            ),
            QueryConditionState(condition_id="cond.unperformed_balance"),
        ],
        dialogue_history=[
            DialogueTurn(
                turn_id=1,
                question_id="q1",
                condition_id="cond.payment_made",
                question="是否付款",
                answer="已经付款",
            ),
            DialogueTurn(
                turn_id=2,
                question_id="q2",
                condition_id="cond.unperformed_balance",
                question="是否有余额",
                answer="没有付款。还有余额。",
            ),
        ],
    )
    output = SemanticConditionProjector(_mapping_catalog()).project(state, [])
    assert _status_of(output, "cond.payment_made") is ConditionStatus.NOT_SATISFIED
    assert _status_of(output, "cond.unperformed_balance") is ConditionStatus.SATISFIED
    assert len(state.condition_states) == 2
    payment = next(
        item for item in output.condition_states if item.condition_id == "cond.payment_made"
    )
    assert any("按更新轮次裁决" in reason for reason in payment.mapping_reasons)


def test_calibration_requires_labels_and_is_monotone():
    from casepath.adapters.conditions.confidence_calibration import ConfidenceCalibrator

    fitted = ConfidenceCalibrator.fit(
        [(0.5, False), (0.9, False), (0.9, True)], dataset_id="heldout-v1"
    )
    assert 0 < fitted.transform(0.5) <= fitted.transform(0.9) < 0.9
    with pytest.raises(ValueError):
        ConfidenceCalibrator.fit([], dataset_id="empty")


def test_returned_paths_exist_in_projection_and_fulltext_has_no_fake_path():
    catalog, graph = _fixture_projection()
    parts = components(catalog)
    rules = parts["rules"].retrieve(gym_state())
    projected = parts["project"].project(gym_state(), rules)
    bundle = parts["cases"].retrieve(projected, rules)
    keys = {}
    for node in graph["nodes"]:
        keys.setdefault(node["object_id"], []).append(node["key"])
    edges = {(item["source"], item["type"], item["target"]) for item in graph["edges"]}

    def exists(node_ids, edge_types):
        for left, right, rel in zip(node_ids[:-1], node_ids[1:], edge_types, strict=True):
            if not any(
                (source, rel, target) in edges
                for source in keys.get(left, [])
                for target in keys.get(right, [])
            ):
                return False
        return True

    for ref in (
        *rules,
        *bundle.support_case_refs,
        *bundle.limiting_case_refs,
        *bundle.boundary_case_refs,
    ):
        if (
            "graph" not in ref.retrieval_channels
            and "applies" not in ref.retrieval_channels
            and "cites" not in ref.retrieval_channels
        ):
            assert ref.graph_paths == []
        for path in ref.graph_paths:
            assert exists(path.node_ids, path.edge_types)
            assert "HAS_MEMBER" not in path.edge_types or "HAS_GROUP" in path.edge_types
            assert path.edge_types != ["ABOUT"]
            assert path.edge_types != ["APPLIES"]
    evidence = current_round()
    assert evidence is not None
    for stored in evidence.paths_by_case.values():
        for nodes, edge_types, _spans in stored:
            assert exists(nodes, edge_types)
    text_paths = evidence.paths_by_case.get("case.text-only", ())
    assert text_paths == ()


def test_unknown_only_case_is_not_support():
    catalog = fixture_catalog()
    parts = components(catalog)
    rules = parts["rules"].retrieve(gym_state())
    bind_context(catalog.context())
    state = QueryState(
        session_id="unk",
        initial_query="健身房关门",
        condition_states=[
            QueryConditionState(condition_id="cond.payment_made", status=ConditionStatus.SATISFIED),
            QueryConditionState(
                condition_id="cond.performance_impossible", status=ConditionStatus.SATISFIED
            ),
        ],
    )
    bundle = parts["cases"].retrieve(state, rules)
    evidence = current_round()
    assert evidence is not None
    assert "case.unknown-only" in evidence.fused_case_ids
    assert "case.unknown-only" not in {item.object_id for item in bundle.support_case_refs}
    assert "case.unknown-only" not in {item.object_id for item in bundle.limiting_case_refs}
    assert "case.unknown-only" in evidence.undetermined_case_ids


def test_cite_only_case_stays_in_fused_pool():
    catalog = fixture_catalog()
    parts = components(catalog)
    rules = parts["rules"].retrieve(gym_state())
    parts["cases"].retrieve(parts["project"].project(gym_state(), rules), rules)
    evidence = current_round()
    assert evidence is not None
    assert "case.cite-only" in evidence.fused_case_ids
    assert "case.cite-only" in evidence.cite_only_ids
    assert "cites" in (evidence.channels_by_case or {}).get("case.cite-only", ())
    assert "case.cite-only" in evidence.undetermined_case_ids


def test_any_group_stays_compatible_when_one_member_fails():
    cond_a = _condition("cond.any.a", "条件甲", "条件甲是否成立")
    cond_b = _condition("cond.any.b", "条件乙", "条件乙是否成立")
    catalog = build_catalog(
        release_id="any",
        rules=[
            _rule(
                "rule.any.v1",
                "任一核心条件即可",
                ["民事咨询"],
                [cond_a, cond_b],
                ["cond.any.a", "cond.any.b"],
                core_operator=ConditionGroupOperator.ANY,
            )
        ],
        provisions=[_provision("0563", "当事人可以解除合同。")],
        lightweight=[],
        deep=[],
    )
    mixed = [
        QueryConditionState(condition_id="cond.any.a", status=ConditionStatus.NOT_SATISFIED),
        QueryConditionState(condition_id="cond.any.b", status=ConditionStatus.SATISFIED),
    ]
    both_no = [
        QueryConditionState(condition_id="cond.any.a", status=ConditionStatus.NOT_SATISFIED),
        QueryConditionState(condition_id="cond.any.b", status=ConditionStatus.NOT_SATISFIED),
    ]
    assert "rule.any.v1" in _compatible_rules(catalog, mixed, ["rule.any.v1"])
    assert "rule.any.v1" not in _compatible_rules(catalog, both_no, ["rule.any.v1"])


def test_factory_reports_vector_missing_even_with_graph_and_llm():
    catalog = fixture_catalog()

    class FakeGraph:
        def execute_read(self, query, parameters, *, timeout_seconds=None):
            return []

    class FakeLLM:
        def generate_structured(
            self, *, system_prompt, user_prompt, response_model, timeout_seconds=None, stage='default'
        ):
            return ConditionLinkBatch(links=[])

    built = HybridCypherP4Factory(catalog=catalog).build(
        graph_gateway=FakeGraph(), language_model=FakeLLM()
    )
    by_name = {item.capability: item for item in built.capabilities}
    assert by_name["rule_retriever"].mode is CapabilityMode.LIVE
    assert by_name["rule_retriever"].degraded is True
    assert by_name["vector_search"].available is False
    assert by_name["vector_search"].mode is CapabilityMode.DISABLED
    assert "向量" in (by_name["vector_search"].reason or "")
    assert by_name["condition_projector"].degraded is False
    assert by_name["case_comparator"].degraded is True


def test_rule_graph_expansion_uses_real_group_path():
    catalog = fixture_catalog()
    nav = MemoryGraphNavigator(catalog)

    class OneRuleSearch:
        def search(self, text, *, target, context, top_k):
            if target != "rule":
                return MemoryHybridSearch(catalog).search(
                    text, target=target, context=context, top_k=top_k
                )
            return [
                scored_reference(
                    "rule.refund.v1",
                    1.0,
                    channels=["bm25"],
                    components=[ScoreComponent(name="bm25", value=1.0, explanation="stub")],
                    reasons=["stub"],
                )
            ]

    retriever = HybridCypherRuleRetriever(nav, OneRuleSearch(), catalog=catalog, navigator=nav)
    refs = retriever.retrieve(gym_state())
    neighbor = next(item for item in refs if item.object_id == "rule.nonperformance.v1")
    assert "graph" in neighbor.retrieval_channels
    assert neighbor.graph_paths
    assert all(path.edge_types == ["HAS_GROUP", "HAS_MEMBER"] for path in neighbor.graph_paths)
    refund = next(item for item in refs if item.object_id == "rule.refund.v1")
    assert refund.graph_paths == []
