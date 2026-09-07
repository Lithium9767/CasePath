"""供应商参数、阶段预算与澄清快路径回归，不调用网络。"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from casepath.adapters.explanation.evidence_explanation_planner import EvidenceExplanationPlanner
from casepath.adapters.infrastructure.structured_llm import OpenAICompatibleStructuredLanguageModel
from casepath.adapters.questioning.question_cards import option_status
from casepath.adapters.retrieval.retrieval_catalog import load_canonical_catalog
from casepath.application.errors import InvalidComponentOutput
from casepath.config import RuntimeSettings, StructuredOutputMode
from casepath.config.settings import LLMStagePolicy
from casepath.contracts import ComparisonBundle, ContractModel, QueryState, RetrievalBundle


class Reply(ContractModel):
    text: str


class Client:
    def __init__(self, finish="stop"):
        self.options = []
        self.calls = []
        self.finish = finish
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=self.finish, message=SimpleNamespace(content='{"text":"ok"}')
                )
            ]
        )


@pytest.mark.parametrize(
    "provider,key", [("deepseek", "max_tokens"), ("openai_compatible", "max_completion_tokens")]
)
def test_provider_parameters_and_stage_budget(provider, key):
    client = Client()
    model = OpenAICompatibleStructuredLanguageModel(
        client,
        model="test",
        default_timeout_seconds=30,
        max_completion_tokens=2048,
        output_mode=StructuredOutputMode.JSON_OBJECT,
        provider=provider,
        stage_policies={
            "explanation": LLMStagePolicy(timeout_seconds=60, max_tokens=1800, max_retries=0)
        },
    )
    model.generate_structured(
        system_prompt="JSON", user_prompt="test", response_model=Reply, stage="explanation"
    )
    sent = client.calls[0]
    assert sent[key] == 1800
    assert ("max_tokens" in sent) != ("max_completion_tokens" in sent)
    assert sent["timeout"] == 60
    assert client.options == [{"max_retries": 0}]
    if provider == "deepseek":
        assert sent["extra_body"] == {"thinking": {"type": "disabled"}}
    else:
        assert "extra_body" not in sent


def test_length_finish_is_rejected_even_when_json_parses():
    model = OpenAICompatibleStructuredLanguageModel(
        Client("length"),
        model="test",
        default_timeout_seconds=30,
        max_completion_tokens=256,
        output_mode=StructuredOutputMode.JSON_OBJECT,
    )
    with pytest.raises(InvalidComponentOutput, match="长度上限"):
        model.generate_structured(system_prompt="JSON", user_prompt="test", response_model=Reply)


def test_partial_stage_config_preserves_other_defaults():
    settings = RuntimeSettings.from_env(
        {"CASEPATH_LLM_STAGE_POLICIES": json.dumps({"projection": {"thinking": "enabled"}})}
    )
    assert settings.llm_stage_policies["projection"].thinking == "enabled"
    assert settings.llm_stage_policies["explanation"].max_tokens == 1800
    assert settings.llm_stage_policies["projection"].max_retries == 0
    with pytest.raises(ValueError):
        RuntimeSettings.from_env({"CASEPATH_LLM_STAGE_POLICIES": '{"typo":{}}'})


def test_clarification_does_not_call_explanation_model():
    class BrokenModel:
        def generate_structured(self, **kwargs):
            raise AssertionError("澄清轮不应调用完整解释模型")

    catalog = load_canonical_catalog(Path("data"))
    result = EvidenceExplanationPlanner(catalog, BrokenModel()).build(
        QueryState(
            session_id="fast", initial_query="还有3000元没消费", status="NEEDS_CLARIFICATION"
        ),
        RetrievalBundle(),
        ComparisonBundle(),
    )
    assert "现在还没有形成完整解释" in result.main_explanation
    assert not result.conditional_branches


def test_refund_request_does_not_mean_contract_ended():
    assert option_status("cond.contract_terminated", "我只提过退款，对方没同意").value == "UNKNOWN"
    assert option_status("cond.contract_terminated", "还没明确说过").value == "UNKNOWN"
