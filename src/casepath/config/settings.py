"""CasePath 正式运行配置。

P1 负责把环境变量转换成经过校验的连接参数；P4 只能接收已经构造好的端口，
不能读取环境变量、API Key 或数据库密码。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from os import environ

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class RuntimeMode(StrEnum):
    DEMO = "demo"
    LIVE = "live"


class StructuredOutputMode(StrEnum):
    """兼容模型使用 JSON Object，支持严格模式的模型使用 JSON Schema。"""

    JSON_OBJECT = "json_object"
    JSON_SCHEMA = "json_schema"


class RuntimeSettings(BaseModel):
    """由环境变量加载的不可变配置；SecretStr 可避免日志和 repr 泄漏凭据。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: RuntimeMode = RuntimeMode.DEMO
    max_question_turns: int = Field(default=3, ge=0, le=20)

    # 沿用 legal-rag 的本地独立端口，但密码必须显式通过环境变量提供。
    neo4j_uri: str | None = "bolt://localhost:7688"
    neo4j_user: str | None = "neo4j"
    neo4j_password: SecretStr | None = None
    neo4j_database: str = "neo4j"
    neo4j_connection_timeout_seconds: float = Field(default=5.0, gt=0)
    neo4j_query_timeout_seconds: float = Field(default=15.0, gt=0)
    neo4j_max_retry_seconds: float = Field(default=3.0, ge=0)

    # legal-rag 当前使用百炼的 OpenAI 兼容接口；地域不同时应显式覆盖 base_url。
    llm_api_key: SecretStr | None = None
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    llm_max_completion_tokens: int = Field(default=2048, ge=1)
    llm_structured_output: StructuredOutputMode = StructuredOutputMode.JSON_OBJECT

    @field_validator(
        "neo4j_uri",
        "neo4j_user",
        "neo4j_database",
        "llm_base_url",
        "llm_model",
    )
    @classmethod
    def reject_blank_config(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("configured connection values must not be blank")
        return value

    @field_validator("neo4j_password", "llm_api_key")
    @classmethod
    def reject_blank_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("configured secrets must not be blank")
        return value

    @property
    def neo4j_configured(self) -> bool:
        return all((self.neo4j_uri, self.neo4j_user, self.neo4j_password))

    @property
    def llm_configured(self) -> bool:
        return self.llm_api_key is not None

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> RuntimeSettings:
        """读取 CASEPATH_*；API Key 兼容已有 DASHSCOPE_API_KEY 环境变量。"""

        source = environ if values is None else values
        names = {
            "mode": "CASEPATH_MODE",
            "max_question_turns": "CASEPATH_MAX_QUESTION_TURNS",
            "neo4j_uri": "CASEPATH_NEO4J_URI",
            "neo4j_user": "CASEPATH_NEO4J_USER",
            "neo4j_password": "CASEPATH_NEO4J_PASSWORD",
            "neo4j_database": "CASEPATH_NEO4J_DATABASE",
            "neo4j_connection_timeout_seconds": "CASEPATH_NEO4J_CONNECTION_TIMEOUT_SECONDS",
            "neo4j_query_timeout_seconds": "CASEPATH_NEO4J_QUERY_TIMEOUT_SECONDS",
            "neo4j_max_retry_seconds": "CASEPATH_NEO4J_MAX_RETRY_SECONDS",
            "llm_api_key": "CASEPATH_LLM_API_KEY",
            "llm_base_url": "CASEPATH_LLM_BASE_URL",
            "llm_model": "CASEPATH_LLM_MODEL",
            "llm_timeout_seconds": "CASEPATH_LLM_TIMEOUT_SECONDS",
            "llm_max_retries": "CASEPATH_LLM_MAX_RETRIES",
            "llm_max_completion_tokens": "CASEPATH_LLM_MAX_COMPLETION_TOKENS",
            "llm_structured_output": "CASEPATH_LLM_STRUCTURED_OUTPUT",
        }
        payload: dict[str, str] = {
            field: source[name] for field, name in names.items() if source.get(name)
        }
        if "llm_api_key" not in payload and source.get("DASHSCOPE_API_KEY"):
            payload["llm_api_key"] = source["DASHSCOPE_API_KEY"]
        return cls.model_validate(payload)
