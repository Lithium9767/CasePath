"""P1 配置到真实 P4 端口的联调；替换外部 SDK，不发送网络请求。"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from casepath.adapters.structured_llm import OpenAICompatibleStructuredLanguageModel
from casepath.api.configured import create_configured_app
from casepath.application.errors import LanguageModelUnavailable
from casepath.bootstrap import build_live_runtime
from casepath.config import RuntimeSettings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    # 不让开发者本机真实 Key/数据库配置进入测试，不记录任何真实凭据。
    for name in tuple(os.environ):
        if name.startswith("CASEPATH_") or name == "DASHSCOPE_API_KEY":
            monkeypatch.delenv(name)


def test_configured_app_defaults_to_demo():
    with TestClient(create_configured_app()) as client:
        caps = {item["capability"]: item for item in client.get("/v1/capabilities").json()}
        assert caps["condition_projector"]["mode"] == "DEMO"
        assert not caps["llm"]["available"]


def test_required_llm_without_key_fails_before_catalog_load(monkeypatch):
    monkeypatch.setenv("CASEPATH_MODE", "live")
    monkeypatch.setenv("CASEPATH_LLM_REQUIRED", "true")
    monkeypatch.setenv("CASEPATH_DATA_ROOT", "nonexistent-data")
    with pytest.raises(LanguageModelUnavailable, match="CASEPATH_LLM_API_KEY"):
        create_configured_app()


def test_required_llm_initialization_failure_closes_graph():
    closed = []
    graph = SimpleNamespace(close=lambda: closed.append(True))

    def fail(settings):
        raise LanguageModelUnavailable("测试初始化失败")

    with pytest.raises(LanguageModelUnavailable):
        build_live_runtime(
            RuntimeSettings(
                mode="live",
                llm_required=True,
                llm_api_key="test-key",
                neo4j_password="test-password",
            ),
            None,  # 失败必须发生在 P4 工厂调用之前。
            graph_factory=lambda settings: graph,
            language_model_factory=fail,
        )
    assert closed == [True]


@pytest.mark.parametrize("provider_fails", [False, True])
def test_configured_llm_reaches_p4_and_is_closed(monkeypatch, provider_fails):
    calls = []
    connected = {}
    closed = []

    def complete(**kwargs):
        calls.append(kwargs)
        if provider_fails:
            raise TimeoutError("private-provider-details")
        content = '{"links": []}' if len(calls) == 2 else (
            '{"paragraphs":[{"text":"根据你提供的原话，尚需进一步核对相关条件。",'
            '"evidence_ids":[],"fact_ids":["fact.initial.query"]}],"branches":[]}'
        )
        if len(calls) == 1:
            import json
            candidates = json.loads(kwargs["messages"][1]["content"])["candidates"]
            content = json.dumps({"items": [{"rule_id": item["rule_id"],
                "relevance": "GENERAL", "reason": "测试候选"} for item in candidates]})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    sdk = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=complete)),
        close=lambda: closed.append(True),
    )

    def connect(**kwargs):
        connected.update(kwargs)
        return OpenAICompatibleStructuredLanguageModel(
            sdk,
            model=kwargs["model"],
            default_timeout_seconds=kwargs["timeout_seconds"],
            max_completion_tokens=kwargs["max_completion_tokens"],
            output_mode=kwargs["output_mode"],
        )

    monkeypatch.setattr(OpenAICompatibleStructuredLanguageModel, "connect", connect)
    for name, value in {
        "CASEPATH_MODE": "live",
        "CASEPATH_LLM_REQUIRED": "true",
        "CASEPATH_MAX_QUESTION_TURNS": "0",
        "CASEPATH_LLM_API_KEY": "test-only-key",
        "CASEPATH_LLM_MODEL": "test-model",
        "CASEPATH_LLM_BASE_URL": "https://provider.invalid/v1",
        "CASEPATH_LLM_TIMEOUT_SECONDS": "7",
        "CASEPATH_LLM_MAX_RETRIES": "0",
        "CASEPATH_LLM_MAX_COMPLETION_TOKENS": "300",
        "CASEPATH_DATA_ROOT": str(Path(__file__).resolve().parents[1] / "data"),
    }.items():
        monkeypatch.setenv(name, value)
    with TestClient(create_configured_app()) as client:
        caps = {item["capability"]: item for item in client.get("/v1/capabilities").json()}
        assert caps["llm"]["available"]
        assert caps["condition_projector"]["mode"] == "LIVE"
        assert not caps["vector_search"]["available"]
        assert not calls  # 装配及健康探测不能产生模型费用。
        response = client.post("/v1/sessions", json={"query": "健身房办了卡，已经付款，关门了"})
        assert response.status_code == (503 if provider_fails else 201), response.text
        assert "private-provider-details" not in response.text
        assert len(calls) == (1 if provider_fails else 3)
        assert calls[0]["model"] == "test-model"
        assert calls[0]["timeout"] == 7
        assert calls[0]["max_completion_tokens"] == 300
    assert connected["api_key"] == "test-only-key"
    assert connected["base_url"] == "https://provider.invalid/v1"
    assert connected["max_retries"] == 0
    assert closed == [True]
