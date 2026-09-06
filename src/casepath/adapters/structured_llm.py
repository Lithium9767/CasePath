"""OpenAI 兼容的结构化 LLM 客户端适配器。

P1 只保证调用超时、JSON格式和Pydantic合同；Prompt与条件判断由P4提供。
"""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import ValidationError

from casepath.application.errors import InvalidComponentOutput, LanguageModelUnavailable
from casepath.config import StructuredOutputMode
from casepath.contracts import ContractModel

ContractT = TypeVar("ContractT", bound=ContractModel)


class OpenAICompatibleStructuredLanguageModel:
    def __init__(
        self,
        client: Any,
        *,
        model: str,
        default_timeout_seconds: float,
        max_completion_tokens: int,
        output_mode: StructuredOutputMode,
    ) -> None:
        self._client = client
        self._model = model
        self._default_timeout_seconds = default_timeout_seconds
        self._max_completion_tokens = max_completion_tokens
        self._output_mode = output_mode
        self._closed = False

    @classmethod
    def connect(
        cls,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_retries: int,
        max_completion_tokens: int,
        output_mode: StructuredOutputMode,
    ) -> OpenAICompatibleStructuredLanguageModel:
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout_seconds,
                max_retries=max_retries,
            )
        except Exception as exc:
            raise LanguageModelUnavailable("无法初始化结构化语言模型客户端") from exc
        return cls(
            client,
            model=model,
            default_timeout_seconds=timeout_seconds,
            max_completion_tokens=max_completion_tokens,
            output_mode=output_mode,
        )

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ContractT],
        timeout_seconds: float | None = None,
    ) -> ContractT:
        timeout = self._default_timeout_seconds if timeout_seconds is None else timeout_seconds
        if timeout <= 0:
            raise ValueError("timeout_seconds must be positive")
        schema = response_model.model_json_schema()
        if self._output_mode == StructuredOutputMode.JSON_SCHEMA:
            response_format: dict[str, object] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__[:64],
                    "strict": True,
                    "schema": schema,
                },
            }
            effective_system_prompt = system_prompt
        else:
            response_format = {"type": "json_object"}
            effective_system_prompt = (
                f"{system_prompt}\n"
                "仅输出一个 JSON 对象，不要输出 Markdown 或额外说明。"
                f"输出必须符合以下 JSON Schema：{json.dumps(schema, ensure_ascii=False)}"
            )
        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": effective_system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=response_format,
                max_completion_tokens=self._max_completion_tokens,
                timeout=timeout,
            )
        except Exception as exc:
            raise LanguageModelUnavailable("结构化语言模型调用失败") from exc
        try:
            content = completion.choices[0].message.content
            if not isinstance(content, str) or not content.strip():
                raise ValueError("model returned empty structured content")
            return response_model.model_validate_json(content)
        except (ValidationError, ValueError, AttributeError, IndexError, TypeError) as exc:
            raise InvalidComponentOutput("结构化语言模型输出不符合目标合同") from exc

    def close(self) -> None:
        if not self._closed:
            close = getattr(self._client, "close", None)
            if callable(close):
                close()
            self._closed = True
