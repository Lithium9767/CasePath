import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from casepath.adapters.demo import build_demo_workflow
from casepath.adapters.infrastructure.structured_llm import OpenAICompatibleStructuredLanguageModel
from casepath.adapters.questioning.key_condition_policy import (
    KeyConditionQuestionPolicy,
    QuestionDraft,
)
from casepath.application.errors import (
    InvalidComponentOutput,
    LanguageModelUnavailable,
    QuestionGenerationFailed,
)
from casepath.config import StructuredOutputMode
from casepath.contracts import ContractModel, QueryConditionState, QueryState
from casepath.workflow import CasePathWorkflow


def fixture_policy(outputs):
    class Model:
        def generate_structured(self, **kwargs):
            self.inputs.append(json.loads(kwargs["user_prompt"]))
            item = next(self.outputs)
            if isinstance(item, Exception):
                raise item
            return QuestionDraft(condition_id="cond.test", askable=True, question=item, reason="核实")
    model = Model()
    model.inputs, model.outputs = [], iter(outputs)
    catalog = SimpleNamespace(conditions={"cond.test": SimpleNamespace(predicate="是否发送通知")})
    state = QueryState(session_id="test", initial_query="退款", condition_states=[QueryConditionState(condition_id="cond.test")])
    return KeyConditionQuestionPolicy(catalog, model), state, model


@pytest.mark.parametrize("second_finish", ["stop", "length"])
def test_projection_length_retry_is_bounded(second_finish):
    class Reply(ContractModel):
        text: str
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="length" if len(calls) == 1 else second_finish,
            message=SimpleNamespace(content='{"text":"ok"}'))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    model = OpenAICompatibleStructuredLanguageModel(client, model="test",
        default_timeout_seconds=30, max_completion_tokens=1400,
        output_mode=StructuredOutputMode.JSON_OBJECT)
    kwargs = {"system_prompt": "JSON", "user_prompt": "test", "response_model": Reply, "stage": "projection"}
    if second_finish == "length":
        with pytest.raises(InvalidComponentOutput):
            model.generate_structured(**kwargs)
    else:
        assert model.generate_structured(**kwargs).text == "ok"
    assert [item["max_completion_tokens"] for item in calls] == [1400, 2800]


def test_two_questions_repaired_with_original_draft():
    bad = "你提出过退款吗？对方怎么回应？"
    policy, state, model = fixture_policy([bad, "你有没有提出退款？"])
    result = policy._generate(state, "cond.test", "未知", "退款")
    assert result.question == "你有没有提出退款？"
    assert model.inputs[1]["rejected_question"] == bad
    assert "最多一个问号" in model.inputs[1]["validation_feedback"]


def test_schema_failure_can_retry():
    policy, state, _ = fixture_policy([InvalidComponentOutput("invalid"), "你有没有提出退款？"])
    assert policy._generate(state, "cond.test", "未知", "退款").askable


def test_repeated_two_questions_not_network_error():
    policy, state, _ = fixture_policy(["提出过退款吗？如何回应？"] * 2)
    with pytest.raises(QuestionGenerationFailed):
        policy._generate(state, "cond.test", "未知", "退款")


@pytest.mark.parametrize("network", [False, True])
def test_workflow_explicit_degradation_only_for_rejected_questions(network):
    class Policy:
        def select(self, *args):
            raise LanguageModelUnavailable("network") if network else QuestionGenerationFailed("rejected")
    base = build_demo_workflow()
    workflow = CasePathWorkflow(replace(base.dependencies, question_policy=Policy()))
    state = QueryState(session_id="test", initial_query="健身房关门")
    if network:
        with pytest.raises(LanguageModelUnavailable):
            workflow.run(state)
    else:
        result = workflow.run(state)
        assert "QUESTION_GENERATION_DEGRADED" in result.trace
        assert "暂未生成合格的追问" in result.explanation_plan.main_explanation
        assert result.explanation_plan.unresolved_condition_ids
        assert result.next_question is None
