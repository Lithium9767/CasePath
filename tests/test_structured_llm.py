import json
from types import SimpleNamespace

import pytest

from casepath.adapters.infrastructure.structured_llm import OpenAICompatibleStructuredLanguageModel
from casepath.application.errors import InvalidComponentOutput, LanguageModelUnavailable
from casepath.config import StructuredOutputMode
from casepath.contracts import ContractModel


class Reply(ContractModel):
    label: str


class FakeCompletions:
    def __init__(self, content='{"label":"ok"}'):
        self.content = content
        self.calls = []
        self.error = None

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class FakeClient:
    def __init__(self, content='{"label":"ok"}'):
        self.completions = FakeCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)
        self.closed = False

    def close(self):
        self.closed = True


def build_model(client, mode=StructuredOutputMode.JSON_OBJECT):
    return OpenAICompatibleStructuredLanguageModel(
        client,
        model="qwen-plus",
        default_timeout_seconds=20,
        max_completion_tokens=256,
        output_mode=mode,
    )


@pytest.mark.parametrize(
    ("mode", "expected_type"),
    [
        (StructuredOutputMode.JSON_OBJECT, "json_object"),
        (StructuredOutputMode.JSON_SCHEMA, "json_schema"),
    ],
)
def test_language_model_validates_contract_and_sends_requested_format(mode, expected_type):
    client = FakeClient()
    model = build_model(client, mode)
    reply = model.generate_structured(
        system_prompt="只进行条件级信息整理",
        user_prompt="输入",
        response_model=Reply,
    )
    assert reply == Reply(label="ok")
    call = client.completions.calls[0]
    assert call["response_format"]["type"] == expected_type
    assert call["timeout"] == 20
    assert call["max_completion_tokens"] == 256
    if mode == StructuredOutputMode.JSON_OBJECT:
        assert "JSON" in call["messages"][0]["content"]
        assert json.dumps(Reply.model_json_schema(), ensure_ascii=False) in call["messages"][0]["content"]


def test_invalid_model_output_is_component_error_not_provider_error():
    model = build_model(FakeClient('{"unexpected":true}'))
    with pytest.raises(InvalidComponentOutput):
        model.generate_structured(
            system_prompt="JSON",
            user_prompt="输入",
            response_model=Reply,
        )


def test_provider_failure_is_sanitized_and_client_closes():
    client = FakeClient()
    client.completions.error = TimeoutError("secret provider endpoint")
    model = build_model(client)
    with pytest.raises(LanguageModelUnavailable) as error:
        model.generate_structured(
            system_prompt="JSON",
            user_prompt="输入",
            response_model=Reply,
        )
    assert "secret provider endpoint" not in str(error.value)
    model.close()
    model.close()
    assert client.closed
