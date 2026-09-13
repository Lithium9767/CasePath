"""Phase 2 missing-fact questioning: stub-model deterministic filters."""

from __future__ import annotations

from typing import ClassVar

import pytest

from casepath.adapters.questioning.key_condition_policy import (
    CallBudget,
    KeyConditionQuestionPolicy,
    QuestionDraft,
)
from casepath.adapters.questioning.missing_facts import (
    MissingFactCandidate,
    MissingFactDraft,
    MissingFactIdentifyBatch,
    filter_missing_facts,
    merge_drafts_by_description,
    question_bundles_known_amounts,
    validate_missing_fact_drafts,
)
from casepath.adapters.retrieval.retrieval_catalog import RuleScope, bind_scope, reset_scope
from casepath.application.errors import QuestionGenerationFailed
from casepath.application.runtime_meta import (
    CorrectionLink,
    SessionRuntimeMeta,
    bind_runtime_meta,
    reset_runtime_meta,
)
from casepath.contracts import (
    ComparisonBundle,
    ConditionStatus,
    QueryConditionState,
    QueryState,
    RetrievalBundle,
    ScoreComponent,
    ScoredReference,
    UserFact,
)


def _bundle(rule_id="rule.x"):
    return RetrievalBundle(
        rule_refs=[
            ScoredReference(
                object_id=rule_id,
                score=1.0,
                retrieval_channels=["bm25"],
                score_components=[ScoreComponent(name="rrf", value=1.0, explanation="t")],
                reasons=["t"],
            )
        ]
    )


def _catalog(condition_ids: list[str], *, title="测试规则"):
    members = list(condition_ids)

    class Catalog:
        rules: ClassVar[dict] = {
            "rule.x": type(
                "R",
                (),
                {
                    "groups": [
                        type(
                            "G",
                            (),
                            {"role": "CORE", "operator": "ALL", "member_ids": members},
                        )()
                    ],
                    "condition_ids": members,
                    "title": title,
                },
            )()
        }
        conditions: ClassVar[dict] = {
            cid: type(
                "C",
                (),
                {
                    "predicate": cid,
                    "label": cid,
                    "user_answerable": True,
                },
            )()
            for cid in members
        }

    return Catalog()


class ScriptedModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate_structured(self, **kwargs):
        self.calls.append(kwargs.get("stage"))
        if not self.responses:
            raise AssertionError("unexpected model call")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_merge_one_fact_two_conditions():
    drafts = [
        MissingFactDraft(
            condition_ids=["cond.performance_impossible"],
            description="新机构课程是否与原课程相同",
            status="MISSING",
            why_needed="区分替代履行",
        ),
        MissingFactDraft(
            condition_ids=["cond.alternative_performance"],
            description="新机构课程是否与原课程相同",
            status="ALREADY_ANSWERED",
            checked_fact_ids=["fact.1"],
            why_needed="区分替代履行",
        ),
    ]
    merged = merge_drafts_by_description(drafts)
    assert len(merged) == 1
    assert set(merged[0].condition_ids) == {
        "cond.performance_impossible",
        "cond.alternative_performance",
    }
    assert merged[0].status == "ALREADY_ANSWERED"


def test_filter_never_promotes_uncertain():
    items = [
        MissingFactCandidate(
            candidate_id="mf.1",
            condition_ids=["cond.a"],
            description="x",
            status="UNCERTAIN",
            why_needed="y",
        )
    ]
    askable, excluded = filter_missing_facts(items)
    assert askable == []
    assert excluded == {"UNCERTAIN": 1}


def test_amount_bundle_detector():
    assert question_bundles_known_amounts(
        "有没有恢复营业安排，还剩5000元吗？",
        ["我充了5000元，剩余3000元"],
        "是否有恢复营业安排",
    )
    assert not question_bundles_known_amounts(
        "店关门后有没有恢复营业安排？",
        ["我充了5000元，剩余3000元"],
        "是否有恢复营业安排",
    )


def test_inheritance_already_answered_skips_generation():
    model = ScriptedModel(
        [
            MissingFactIdentifyBatch(
                items=[
                    MissingFactDraft(
                        condition_ids=["cond.succession.first_order_heir_exists"],
                        description="被继承人是否还有其他子女",
                        status="ALREADY_ANSWERED",
                        checked_fact_ids=["fact.heirs"],
                        why_needed="继承人范围",
                    )
                ]
            )
        ]
    )
    catalog = _catalog(["cond.succession.first_order_heir_exists"], title="继承")
    token = bind_scope(
        RuleScope(("rule.x",), (), (), ("cond.succession.first_order_heir_exists",))
    )
    meta = SessionRuntimeMeta()
    mt = bind_runtime_meta(meta)
    try:
        policy = KeyConditionQuestionPolicy(catalog, language_model=model)
        state = QueryState(
            session_id="s-heir",
            initial_query="父亲去世了",
            user_facts=[
                UserFact(
                    fact_id="fact.heirs",
                    text="父亲只有我和姐姐两个子女",
                    source_turn=0,
                )
            ],
            condition_states=[
                QueryConditionState(
                    condition_id="cond.succession.first_order_heir_exists",
                    status=ConditionStatus.UNKNOWN,
                    mapping_reasons=["t"],
                )
            ],
        )
        question = policy.select(state, _bundle(), ComparisonBundle())
        assert question is None
        assert model.calls == ["missing_facts"]
        assert meta.last_stop_reason == "ALL_ALREADY_ANSWERED"
    finally:
        reset_runtime_meta(mt)
        reset_scope(token)


def test_training_cross_condition_course_dedup():
    model = ScriptedModel(
        [
            MissingFactIdentifyBatch(
                items=[
                    MissingFactDraft(
                        condition_ids=[
                            "cond.performance_impossible",
                            "cond.alternative_performance",
                        ],
                        description="新机构课程内容是否与原课程相同",
                        status="ALREADY_ANSWERED",
                        checked_fact_ids=["fact.alt"],
                        why_needed="替代服务差异",
                    ),
                    MissingFactDraft(
                        condition_ids=["cond.contract_terminated"],
                        description="是否已提出退款并协商解除",
                        status="MISSING",
                        why_needed="解除程序",
                    ),
                ]
            ),
            QuestionDraft(
                condition_id="cond.contract_terminated",
                askable=True,
                question="你有没有向机构提出退款？",
                reason="这能帮助分清目前是否已经进入协商解除阶段。",
            ),
        ]
    )
    catalog = _catalog(
        [
            "cond.performance_impossible",
            "cond.alternative_performance",
            "cond.contract_terminated",
        ],
        title="培训",
    )
    token = bind_scope(
        RuleScope(
            ("rule.x",),
            (),
            (),
            (
                "cond.performance_impossible",
                "cond.alternative_performance",
                "cond.contract_terminated",
            ),
        )
    )
    meta = SessionRuntimeMeta()
    mt = bind_runtime_meta(meta)
    try:
        policy = KeyConditionQuestionPolicy(catalog, language_model=model)
        state = QueryState(
            session_id="s-train",
            initial_query="培训转课想退款",
            user_facts=[
                UserFact(
                    fact_id="fact.alt",
                    text="新机构课程内容也不同，我没有同意转课。",
                    source_turn=0,
                )
            ],
            condition_states=[
                QueryConditionState(
                    condition_id=cid,
                    status=ConditionStatus.UNKNOWN,
                    mapping_reasons=["t"],
                )
                for cid in [
                    "cond.performance_impossible",
                    "cond.alternative_performance",
                    "cond.contract_terminated",
                ]
            ],
        )
        question = policy.select(state, _bundle(), ComparisonBundle())
        assert question is not None
        assert question.condition_id == "cond.contract_terminated"
        assert "课程" not in question.question
        assert model.calls == ["missing_facts", "question"]
    finally:
        reset_runtime_meta(mt)
        reset_scope(token)


def test_gym_rejects_amount_bundling_then_accepts_clean_question():
    model = ScriptedModel(
        [
            MissingFactIdentifyBatch(
                items=[
                    MissingFactDraft(
                        condition_ids=["cond.performance_impossible"],
                        description="是否有恢复营业安排",
                        status="MISSING",
                        why_needed="履行能否继续",
                    )
                ]
            ),
            QuestionDraft(
                condition_id="cond.performance_impossible",
                askable=True,
                question="有没有恢复营业，还剩5000元吗？",
                reason="需要确认能否继续服务。",
            ),
            QuestionDraft(
                condition_id="cond.performance_impossible",
                askable=True,
                question="店关门后有没有恢复营业安排？",
                reason="这能帮助分清是暂时停业还是无法继续服务。",
            ),
        ]
    )
    catalog = _catalog(["cond.performance_impossible"], title="健身")
    token = bind_scope(RuleScope(("rule.x",), (), (), ("cond.performance_impossible",)))
    meta = SessionRuntimeMeta()
    mt = bind_runtime_meta(meta)
    try:
        policy = KeyConditionQuestionPolicy(catalog, language_model=model)
        state = QueryState(
            session_id="s-gym",
            initial_query="健身房退款",
            user_facts=[
                UserFact(
                    fact_id="fact.pay",
                    text="我充了5000元，还有3000元没消费。",
                    source_turn=0,
                )
            ],
            condition_states=[
                QueryConditionState(
                    condition_id="cond.performance_impossible",
                    status=ConditionStatus.UNKNOWN,
                    mapping_reasons=["t"],
                )
            ],
        )
        question = policy.select(state, _bundle(), ComparisonBundle())
        assert question is not None
        assert "5000" not in question.question
        assert "恢复" in question.question
        assert model.calls.count("question") == 2
    finally:
        reset_runtime_meta(mt)
        reset_scope(token)


def test_unable_does_not_rephrase():
    model = ScriptedModel(
        [
            MissingFactIdentifyBatch(
                items=[
                    MissingFactDraft(
                        condition_ids=["cond.succession.estate_mixed_with_common_property"],
                        description="房子是否属于夫妻共同财产",
                        status="USER_UNABLE",
                        checked_fact_ids=["fact.unable"],
                        why_needed="遗产范围",
                    )
                ]
            )
        ]
    )
    catalog = _catalog(
        ["cond.succession.estate_mixed_with_common_property"], title="继承"
    )
    token = bind_scope(
        RuleScope(
            ("rule.x",),
            (),
            (),
            ("cond.succession.estate_mixed_with_common_property",),
        )
    )
    meta = SessionRuntimeMeta(
        unable_condition_ids=["cond.succession.estate_mixed_with_common_property"]
    )
    mt = bind_runtime_meta(meta)
    try:
        # collect excludes unable conditions — so force identify path with empty unable in meta
        # but identify returns USER_UNABLE
        meta.unable_condition_ids = []
        policy = KeyConditionQuestionPolicy(catalog, language_model=model)
        state = QueryState(
            session_id="s-unable",
            initial_query="继承",
            user_facts=[
                UserFact(fact_id="fact.unable", text="不知道房子是否共同财产", source_turn=0)
            ],
            condition_states=[
                QueryConditionState(
                    condition_id="cond.succession.estate_mixed_with_common_property",
                    status=ConditionStatus.UNKNOWN,
                    mapping_reasons=["t"],
                )
            ],
        )
        question = policy.select(state, _bundle(), ComparisonBundle())
        assert question is None
        assert "question" not in model.calls
        assert meta.last_stop_reason == "USER_UNABLE_TO_ANSWER"
    finally:
        reset_runtime_meta(mt)
        reset_scope(token)


def test_correction_uses_effective_facts_in_identify_context():
    captured = {}

    class Model:
        def generate_structured(self, **kwargs):
            import json

            captured["prompt"] = json.loads(kwargs["user_prompt"])
            return MissingFactIdentifyBatch(items=[])

    catalog = _catalog(["cond.payment_made"])
    token = bind_scope(RuleScope(("rule.x",), (), (), ("cond.payment_made",)))
    meta = SessionRuntimeMeta(
        corrections=[
            CorrectionLink(
                target_fact_id="fact.old",
                replacement_fact_id="fact.new",
                condition_id="cond.payment_made",
                reason="纠正金额",
            )
        ]
    )
    mt = bind_runtime_meta(meta)
    try:
        policy = KeyConditionQuestionPolicy(catalog, language_model=Model())
        state = QueryState(
            session_id="s-corr",
            initial_query="退费",
            user_facts=[
                UserFact(fact_id="fact.old", text="我交了5000元", source_turn=0),
                UserFact(fact_id="fact.new", text="金额说错了，是6000元", source_turn=0),
            ],
            condition_states=[
                QueryConditionState(
                    condition_id="cond.payment_made",
                    status=ConditionStatus.UNKNOWN,
                    mapping_reasons=["t"],
                )
            ],
        )
        policy.select(state, _bundle(), ComparisonBundle())
        facts = captured["prompt"]["effective_facts"]
        by_id = {item["fact_id"]: item for item in facts}
        assert by_id["fact.old"]["evidence_role"] == "partially_superseded"
        assert "cond.payment_made" in by_id["fact.old"]["inactive_for_conditions"]
        assert by_id["fact.new"]["evidence_role"] == "effective"
    finally:
        reset_runtime_meta(mt)
        reset_scope(token)


def test_all_invalid_identify_items_fail_loudly():
    model = ScriptedModel(
        [
            MissingFactIdentifyBatch(
                items=[
                    MissingFactDraft(
                        condition_ids=["cond.invented"],
                        description="虚构条件事实",
                        status="MISSING",
                        why_needed="x",
                    )
                ]
            )
        ]
    )
    catalog = _catalog(["cond.payment_made"])
    token = bind_scope(RuleScope(("rule.x",), (), (), ("cond.payment_made",)))
    meta = SessionRuntimeMeta()
    mt = bind_runtime_meta(meta)
    try:
        policy = KeyConditionQuestionPolicy(catalog, language_model=model)
        state = QueryState(
            session_id="s-bad",
            initial_query="退费",
            condition_states=[
                QueryConditionState(
                    condition_id="cond.payment_made",
                    status=ConditionStatus.UNKNOWN,
                    mapping_reasons=["t"],
                )
            ],
        )
        with pytest.raises(QuestionGenerationFailed):
            policy.select(state, _bundle(), ComparisonBundle())
        assert meta.last_stop_reason == "GENERATION_FAILED"
    finally:
        reset_runtime_meta(mt)
        reset_scope(token)


def test_identify_and_generate_share_budget():
    model = ScriptedModel(
        [
            MissingFactIdentifyBatch(
                items=[
                    MissingFactDraft(
                        condition_ids=["cond.payment_made"],
                        description="实际支付金额",
                        status="MISSING",
                        why_needed="付款",
                    )
                ]
            ),
            QuestionDraft(
                condition_id="cond.payment_made",
                askable=True,
                question="你实际付了多少钱？",
                reason="需要核对付款金额。",
            ),
        ]
    )
    catalog = _catalog(["cond.payment_made"])
    token = bind_scope(RuleScope(("rule.x",), (), (), ("cond.payment_made",)))
    meta = SessionRuntimeMeta()
    mt = bind_runtime_meta(meta)
    try:
        policy = KeyConditionQuestionPolicy(
            catalog, language_model=model, generation_budget=2
        )
        state = QueryState(
            session_id="s-budget",
            initial_query="退费",
            condition_states=[
                QueryConditionState(
                    condition_id="cond.payment_made",
                    status=ConditionStatus.UNKNOWN,
                    mapping_reasons=["t"],
                )
            ],
        )
        question = policy.select(state, _bundle(), ComparisonBundle())
        assert question is not None
        assert model.calls == ["missing_facts", "question"]
    finally:
        reset_runtime_meta(mt)
        reset_scope(token)


def test_validate_drops_unknown_ids_with_reasons():
    kept, reasons = validate_missing_fact_drafts(
        [
            MissingFactDraft(
                condition_ids=["cond.ok", "cond.bad"],
                description="x",
                status="MISSING",
                checked_fact_ids=["f.ok", "f.bad"],
                checked_question_ids=["q.ok", "q.bad"],
                why_needed="y",
            )
        ],
        allowed_condition_ids={"cond.ok"},
        known_fact_ids={"f.ok"},
        known_question_ids={"q.ok"},
    )
    assert len(kept) == 1
    assert kept[0].condition_ids == ["cond.ok"]
    assert kept[0].checked_fact_ids == ["f.ok"]
    assert any("dropped_unknown_condition" in item for item in reasons)



def test_training_identify_failure_does_not_invent_missing_facts():
    """Identify schema failure must not re-ask course sameness after coverage."""

    class BoomIdentifyThenOkQuestion:
        def __init__(self):
            self.calls = []

        def generate_structured(self, **kwargs):
            self.calls.append(kwargs.get("stage"))
            from casepath.application.errors import InvalidComponentOutput

            if kwargs.get("stage") == "missing_facts":
                raise InvalidComponentOutput("schema_invalid")
            return QuestionDraft(
                condition_id="cond.contract_terminated",
                askable=True,
                question="你有没有向机构提出退款？",
                reason="确认是否已进入解除协商。",
            )

    catalog = _catalog(
        [
            "cond.performance_impossible",
            "cond.alternative_performance",
            "cond.contract_terminated",
        ],
        title="培训",
    )
    model = BoomIdentifyThenOkQuestion()
    token = bind_scope(
        RuleScope(
            ("rule.x",),
            (),
            (),
            (
                "cond.performance_impossible",
                "cond.alternative_performance",
                "cond.contract_terminated",
            ),
        )
    )
    meta = SessionRuntimeMeta()
    mt = bind_runtime_meta(meta)
    try:
        policy = KeyConditionQuestionPolicy(catalog, language_model=model, generation_budget=8)
        state = QueryState(
            session_id="s-train-fail",
            initial_query="培训转课想退款",
            user_facts=[
                UserFact(
                    fact_id="fact.alt",
                    text="新机构课程内容也不同，我没有同意转课。",
                    source_turn=0,
                )
            ],
            condition_states=[
                QueryConditionState(
                    condition_id=cid,
                    status=ConditionStatus.UNKNOWN,
                    mapping_reasons=["t"],
                )
                for cid in [
                    "cond.performance_impossible",
                    "cond.alternative_performance",
                    "cond.contract_terminated",
                ]
            ],
        )
        with pytest.raises(QuestionGenerationFailed):
            policy.select(state, _bundle(), ComparisonBundle())
        assert model.calls == ["missing_facts", "missing_facts"]
        assert meta.last_stop_reason == "GENERATION_FAILED"
    finally:
        reset_runtime_meta(mt)
        reset_scope(token)


def test_same_condition_different_missing_fact_is_retained():
    """课程差异已回答，不影响同条件下恢复营业安排的候选。"""
    drafts = [MissingFactDraft(
        condition_ids=["cond.performance_impossible"], description=description,
        status=status, why_needed="比较服务安排",
    ) for description, status in [
        ("课程是否相同", "ALREADY_ANSWERED"),
        ("是否有恢复营业安排", "MISSING"),
    ]]
    merged = merge_drafts_by_description(drafts)
    candidates = [MissingFactCandidate(
        candidate_id=f"mf.{i}", **item.model_dump(),
    ) for i, item in enumerate(merged)]
    askable, _ = filter_missing_facts(candidates)
    assert [item.description for item in askable] == ["是否有恢复营业安排"]


@pytest.mark.parametrize("description", ["", "   "])
def test_invalid_description_is_failure_not_empty_success(description):
    class Model:
        def generate_structured(self, **kwargs):
            return MissingFactIdentifyBatch(items=[MissingFactDraft(
                condition_ids=["cond.x"], description=description,
                status="MISSING", why_needed="测试",
            )])
    policy = KeyConditionQuestionPolicy(None, Model())
    budget = CallBudget(4)
    with pytest.raises(QuestionGenerationFailed):
        policy._identify_missing_facts({}, budget)
    assert budget.used == 2


def test_long_valid_description_converts_to_candidate():
    draft = MissingFactDraft(
        condition_ids=["cond.x"], description="事实" * 75,
        status="MISSING", why_needed="测试长度一致",
    )
    candidate = MissingFactCandidate(candidate_id="mf.1", **draft.model_dump())
    assert len(candidate.description) == 150
