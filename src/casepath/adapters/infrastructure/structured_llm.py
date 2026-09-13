"""OpenAI 兼容的结构化 LLM 客户端适配器。

P1 只保证调用超时、JSON格式和Pydantic合同；Prompt与条件判断由P4提供。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, TypeVar
from urllib.parse import urlsplit

from pydantic import ValidationError

from casepath.application.errors import InvalidComponentOutput, LanguageModelUnavailable
from casepath.config import StructuredOutputMode
from casepath.contracts import ContractModel

ContractT = TypeVar("ContractT", bound=ContractModel)


class OpenAICompatibleStructuredLanguageModel:
    # 【调用链】bootstrap._build_language_model → connect → cls(...) → __init__。
    # 本类处理模型协议和结构校验；法律提示词、证据选择仍由 P4 调用方负责。
    def __init__(
        self,
        client: Any,
        *,
        model: str,
        default_timeout_seconds: float,
        max_completion_tokens: int,
        output_mode: StructuredOutputMode,
        provider: str = "openai_compatible",
        stage_policies: dict | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._default_timeout_seconds = default_timeout_seconds
        self._max_completion_tokens = max_completion_tokens
        self._output_mode = output_mode
        self._closed = False
        self._provider = provider
        self._stage_policies = stage_policies or {}

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
        stage_policies: dict | None = None,
    ) -> OpenAICompatibleStructuredLanguageModel:
        try:
            from openai import OpenAI

            client = OpenAI(
                # 创建兼容协议的 SDK 客户端，不是加载模型权重，也不会在这里生成回答。
                # 实际供应商由 base_url 决定，使用该 SDK 不代表一定调用 OpenAI 的模型。
                api_key=api_key,
                base_url=base_url,
                timeout=timeout_seconds,
                max_retries=max_retries,
            )
        except Exception as exc:
            raise LanguageModelUnavailable("无法初始化结构化语言模型客户端") from exc
        return cls(
            # 包装 SDK 客户端，同时记录默认模型和输出策略，交给 P4 重复使用。
            client,
            model=model,
            default_timeout_seconds=timeout_seconds,
            max_completion_tokens=max_completion_tokens,
            output_mode=output_mode,
            provider="deepseek"
            if urlsplit(base_url).hostname == "api.deepseek.com"
            else "openai_compatible",
            stage_policies=stage_policies,
        )

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ContractT],
        timeout_seconds: float | None = None,
        stage: str = "default",
        _retry_token_limit: int | None = None,
    ) -> ContractT:
        # 【在线模型入口】规则相关性筛选、条件投影、完整解释等阶段按需调用本方法。
        # response_model 是调用方要求的 Pydantic 类型；返回该类型实例，不是原始文本。
        # stage 用于选择阶段配置，不能理解为每次都依次执行所有模型阶段。
        policy = self._stage_policies.get(stage)
        timeout = (
            (policy.timeout_seconds if policy else self._default_timeout_seconds)
            if timeout_seconds is None
            else timeout_seconds
        )
        limit = policy.max_tokens if policy else self._max_completion_tokens
        if _retry_token_limit is not None:
            limit = _retry_token_limit
        if timeout <= 0:
            raise ValueError("timeout_seconds must be positive")
        schema = response_model.model_json_schema()
        # 从合同生成输出结构要求；格式正确不等于法律内容和引用语义已经正确。
        if self._output_mode == StructuredOutputMode.JSON_SCHEMA:
            # 供应商支持时使用结构化输出参数；否则下面通过 JSON 模式与提示词约束。
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
        # 兼容协议不意味着参数通用：DeepSeek 使用 max_tokens，并显式控制思考模式。
        options = {"max_tokens" if self._provider == "deepseek" else "max_completion_tokens": limit}
        if self._provider == "deepseek" and policy:
            options["extra_body"] = {"thinking": {"type": policy.thinking}}
        client = (
            self._client.with_options(max_retries=policy.max_retries) if policy else self._client
        )
        started = time.monotonic()
        try:
            completion = client.chat.completions.create(
                # 【真正的网络生成请求】直到这里才把 system/user 提示词发给模型服务。
                # user_prompt 中的证据包由 P4 组织；本客户端不查询 Neo4j，也不自行检索。
                model=self._model,
                messages=[
                    {"role": "system", "content": effective_system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=response_format,
                **options,
                timeout=timeout,
            )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "LLM stage=%s elapsed=%.2f error=%s",
                stage,
                time.monotonic() - started,
                type(exc).__name__,
            )
            raise LanguageModelUnavailable("结构化语言模型调用失败") from exc
        try:
            # 不把截断的 JSON 当成随机解析错误；不记录正文、密钥或供应商错误原文。
            if getattr(completion.choices[0], "finish_reason", None) == "length":
                # 多轮条件映射 / 缺失事实识别可能随上下文增长超出首次预算。
                # 仅明确截断时扩容一次，不接受残缺JSON，不对网络/其他校验错误盲目重试，上限8192。
                expandable = {"projection", "missing_facts"}
                if stage in expandable and _retry_token_limit is None and limit < 8192:
                    expanded_limit = min(max(limit * 2, 2048), 8192)
                    logging.getLogger("uvicorn.error").warning(
                        "LLM stage=%s reason=output_truncated retry_tokens=%s",
                        stage,
                        expanded_limit,
                    )
                    return self.generate_structured(
                        system_prompt=system_prompt, user_prompt=user_prompt,
                        response_model=response_model, timeout_seconds=timeout_seconds,
                        stage=stage, _retry_token_limit=expanded_limit,
                    )
                raise InvalidComponentOutput("结构化模型输出达到长度上限，请调整阶段预算")
            usage = getattr(completion, "usage", None)
            logging.getLogger(__name__).info(
                "LLM stage=%s elapsed=%.2f input_tokens=%s output_tokens=%s",
                stage,
                time.monotonic() - started,
                getattr(usage, "prompt_tokens", None),
                getattr(usage, "completion_tokens", None),
            )
            content = completion.choices[0].message.content
            if not isinstance(content, str) or not content.strip():
                raise ValueError("model returned empty structured content")
            return response_model.model_validate_json(content)
            # 返回前再次按合同校验 JSON；不符合结构抛异常，不把错误文本当作成功结果。
        except (ValidationError, ValueError, AttributeError, IndexError, TypeError) as exc:
            raise InvalidComponentOutput("结构化语言模型输出不符合目标合同") from exc

    def close(self) -> None:
        # 应用关闭时释放 SDK 的 HTTP 资源；不是在每次生成后销毁客户端。
        if not self._closed:
            close = getattr(self._client, "close", None)
            if callable(close):
                close()
            self._closed = True
