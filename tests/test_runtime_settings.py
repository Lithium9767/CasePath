import pytest
from pydantic import ValidationError

from casepath.config import RuntimeMode, RuntimeSettings, StructuredOutputMode


def test_settings_default_to_demo_without_credentials():
    settings = RuntimeSettings.from_env({})
    assert settings.mode == RuntimeMode.DEMO
    assert not settings.neo4j_configured
    assert not settings.llm_configured


def test_settings_load_casepath_values_and_legacy_dashscope_key():
    settings = RuntimeSettings.from_env(
        {
            "CASEPATH_MODE": "live",
            "CASEPATH_NEO4J_PASSWORD": "graph-secret",
            "DASHSCOPE_API_KEY": "llm-secret",
            "CASEPATH_LLM_STRUCTURED_OUTPUT": "json_schema",
            "CASEPATH_MAX_QUESTION_TURNS": "5",
        }
    )
    assert settings.mode == RuntimeMode.LIVE
    assert settings.neo4j_configured
    assert settings.llm_configured
    assert settings.llm_structured_output == StructuredOutputMode.JSON_SCHEMA
    assert settings.max_question_turns == 5
    assert "graph-secret" not in repr(settings)
    assert "llm-secret" not in repr(settings)


def test_settings_reject_invalid_timeouts():
    with pytest.raises(ValidationError):
        RuntimeSettings(neo4j_query_timeout_seconds=0)
    with pytest.raises(ValidationError):
        RuntimeSettings(llm_api_key=" ")


def test_llm_startup_policy_and_data_path_are_loaded():
    from pathlib import Path

    settings = RuntimeSettings.from_env(
        {
            "CASEPATH_LLM_REQUIRED": "true",
            "CASEPATH_DATA_ROOT": "custom-data",
            "CASEPATH_LLM_API_KEY": "preferred-key",
            "DASHSCOPE_API_KEY": "fallback-key",
        }
    )
    assert settings.llm_required
    assert settings.data_root == Path("custom-data")
    assert settings.llm_api_key.get_secret_value() == "preferred-key"
