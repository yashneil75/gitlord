from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

try:
    from litellm import completion, embedding
    HAS_LITELLM = True
except ImportError:
    HAS_LITELLM = False


from gitlord.schemas import GitlordError


class LiteLLMError(GitlordError):
    pass


class ToolSchemaTranslator:
    def translate(
        self,
        tools: list[dict[str, Any]],
        provider: str = "openai",
    ) -> list[dict[str, Any]]:
        if provider in ("openai", "azure", "together", "groq"):
            return self._to_openai_format(tools)
        elif provider in ("anthropic", "claude"):
            return self._to_anthropic_format(tools)
        elif provider in ("google", "gemini"):
            return self._to_gemini_format(tools)
        return self._to_openai_format(tools)

    def _to_openai_format(
        self, tools: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        result = []
        for t in tools:
            result.append({
                "type": "function",
                "function": {
                    "name": t.get("name", "unknown"),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {}),
                },
            })
        return result

    def _to_anthropic_format(
        self, tools: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        result = []
        for t in tools:
            result.append({
                "name": t.get("name", "unknown"),
                "description": t.get("description", ""),
                "input_schema": t.get("input_schema", {}),
            })
        return result

    def _to_gemini_format(
        self, tools: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return [
            {
                "function_declarations": [
                    {
                        "name": t.get("name", "unknown"),
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {}),
                    }
                    for t in tools
                ]
            }
        ]

    def infer_provider(self, model: str) -> str:
        model_lower = model.lower()
        if any(p in model_lower for p in ("claude", "anthropic")):
            return "anthropic"
        elif any(p in model_lower for p in ("gemini", "google")):
            return "google"
        elif any(p in model_lower for p in ("gpt", "o1", "o3", "azure")):
            return "openai"
        return "openai"


class ModelRouter:
    def __init__(
        self,
        default_model: str = "gpt-4o",
        tool_translator: Optional[ToolSchemaTranslator] = None,
        provider_params: dict[str, Any] | None = None,
        router_config: dict[str, Any] | None = None,
    ) -> None:
        self.default_model = default_model
        self.translator = tool_translator or ToolSchemaTranslator()
        self.provider_params = provider_params or {}
        self.router_config = router_config or {}

    def _chat_once(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        provider = self.translator.infer_provider(model_name)
        chat_kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            **kwargs,
        }
        if tools:
            translated = self.translator.translate(tools, provider)
            chat_kwargs["tools"] = translated
        if self.provider_params:
            chat_kwargs.setdefault("metadata", {}).update(
                self.provider_params
            )
        response = completion(**chat_kwargs)
        return self._normalize_response(response)

    def _chat_with_retry(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        max_retries = self.router_config.get("max_retries", 3)
        retry_delay = self.router_config.get("retry_delay", 1.0)
        fallbacks = self.router_config.get("fallbacks", [])

        models_to_try: list[tuple[str, int]] = [(model_name, max_retries)]
        for fb_model, fb_retries in fallbacks:
            models_to_try.append((fb_model, fb_retries))

        last_error: Exception | None = None
        for attempt_model, retry_count in models_to_try:
            for attempt in range(retry_count):
                try:
                    return self._chat_once(attempt_model, messages, tools, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < retry_count - 1:
                        time.sleep(retry_delay * (2**attempt))

        raise LiteLLMError(str(last_error)) from last_error

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not HAS_LITELLM:
            raise LiteLLMError("litellm is not installed")

        model_name = model or self.default_model
        return self._chat_with_retry(model_name, messages, tools, **kwargs)

    async def _chat_once_async(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        from litellm import acompletion

        provider = self.translator.infer_provider(model_name)
        chat_kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            **kwargs,
        }
        if tools:
            translated = self.translator.translate(tools, provider)
            chat_kwargs["tools"] = translated
        if self.provider_params:
            chat_kwargs.setdefault("metadata", {}).update(
                self.provider_params
            )
        response = await acompletion(**chat_kwargs)
        return self._normalize_response(response)

    async def _chat_with_retry_async(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        max_retries = self.router_config.get("max_retries", 3)
        retry_delay = self.router_config.get("retry_delay", 1.0)
        fallbacks = self.router_config.get("fallbacks", [])

        models_to_try: list[tuple[str, int]] = [(model_name, max_retries)]
        for fb_model, fb_retries in fallbacks:
            models_to_try.append((fb_model, fb_retries))

        last_error: Exception | None = None
        for attempt_model, retry_count in models_to_try:
            for attempt in range(retry_count):
                try:
                    return await self._chat_once_async(
                        attempt_model, messages, tools, **kwargs
                    )
                except Exception as e:
                    last_error = e
                    if attempt < retry_count - 1:
                        await asyncio.sleep(retry_delay * (2**attempt))

        raise LiteLLMError(str(last_error)) from last_error

    async def chat_async(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not HAS_LITELLM:
            raise LiteLLMError("litellm is not installed")

        model_name = model or self.default_model
        return await self._chat_with_retry_async(model_name, messages, tools, **kwargs)

    def embed(
        self,
        text: str,
        model: str | None = None,
    ) -> list[float]:
        if not HAS_LITELLM:
            raise LiteLLMError("litellm is not installed")

        model_name = model or "text-embedding-ada-002"
        try:
            response = embedding(model=model_name, input=[text])
            return response.data[0]["embedding"]
        except Exception as e:
            raise LiteLLMError(str(e)) from e

    async def embed_async(
        self,
        text: str,
        model: str | None = None,
    ) -> list[float]:
        if not HAS_LITELLM:
            raise LiteLLMError("litellm is not installed")

        model_name = model or "text-embedding-ada-002"
        try:
            from litellm import aembedding
            response = await aembedding(model=model_name, input=[text])
            return response.data[0]["embedding"]
        except Exception as e:
            raise LiteLLMError(str(e)) from e

    def _normalize_response(self, response: Any) -> dict[str, Any]:
        choice = response.choices[0]
        msg = choice.message

        normalized: dict[str, Any] = {
            "role": msg.role or "assistant",
            "content": msg.content or "",
            "model": response.model,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            },
        }

        if hasattr(msg, "tool_calls") and msg.tool_calls:
            normalized["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]

        return normalized
