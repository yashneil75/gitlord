from __future__ import annotations

from typing import Any

import pytest

from gitlord.model import ModelRouter, ToolSchemaTranslator, LiteLLMError


class TestToolSchemaTranslator:
    def test_openai_format(self):
        translator = ToolSchemaTranslator()
        tools = [
            {
                "name": "test_tool",
                "description": "A test tool",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]
        result = translator.translate(tools, "openai")
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "test_tool"
        assert result[0]["function"]["description"] == "A test tool"
        assert result[0]["function"]["parameters"] == {
            "type": "object",
            "properties": {},
        }

    def test_anthropic_format(self):
        translator = ToolSchemaTranslator()
        tools = [
            {
                "name": "test_tool",
                "description": "A test tool",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]
        result = translator.translate(tools, "anthropic")
        assert len(result) == 1
        assert result[0]["name"] == "test_tool"
        assert result[0]["description"] == "A test tool"
        assert result[0]["input_schema"] == {"type": "object", "properties": {}}

    def test_gemini_format(self):
        translator = ToolSchemaTranslator()
        tools = [
            {
                "name": "test_tool",
                "description": "A test tool",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]
        result = translator.translate(tools, "google")
        assert len(result) == 1
        assert "function_declarations" in result[0]
        assert result[0]["function_declarations"][0]["name"] == "test_tool"

    def test_infer_provider_anthropic(self):
        translator = ToolSchemaTranslator()
        assert translator.infer_provider("claude-3-5-sonnet") == "anthropic"
        assert translator.infer_provider("anthropic/claude-2") == "anthropic"

    def test_infer_provider_google(self):
        translator = ToolSchemaTranslator()
        assert translator.infer_provider("gemini-pro") == "google"
        assert translator.infer_provider("google/gemini-2.0-flash") == "google"

    def test_infer_provider_openai(self):
        translator = ToolSchemaTranslator()
        assert translator.infer_provider("gpt-4o") == "openai"
        assert translator.infer_provider("o1-preview") == "openai"
        assert translator.infer_provider("azure/gpt-4") == "openai"

    def test_infer_provider_defaults_to_openai(self):
        translator = ToolSchemaTranslator()
        assert translator.infer_provider("unknown-model") == "openai"

    def test_unknown_provider_falls_back_to_openai(self):
        translator = ToolSchemaTranslator()
        tools = [{"name": "t", "input_schema": {"type": "object"}}]
        result = translator.translate(tools, "unknown_provider")
        assert result[0]["type"] == "function"


class TestModelRouter:
    def test_constructor_defaults(self):
        router = ModelRouter()
        assert router.default_model == "gpt-4o"
        assert isinstance(router.translator, ToolSchemaTranslator)
        assert router.provider_params == {}
        assert router.router_config == {}

    def test_constructor_custom(self):
        config = {
            "max_retries": 5,
            "retry_delay": 2.0,
            "fallbacks": [("gpt-3.5-turbo", 2)],
        }
        router = ModelRouter(
            default_model="claude-sonnet",
            provider_params={"user": "test"},
            router_config=config,
        )
        assert router.default_model == "claude-sonnet"
        assert router.provider_params == {"user": "test"}
        assert router.router_config["max_retries"] == 5
        assert router.router_config["fallbacks"] == [("gpt-3.5-turbo", 2)]

    def test_chat_raises_when_litellm_fails(self):
        router = ModelRouter()
        with pytest.raises(LiteLLMError):
            router.chat([{"role": "user", "content": "hi"}])

    def test_chat_async_raises_when_litellm_fails(self):
        router = ModelRouter()
        with pytest.raises(LiteLLMError):
            import asyncio

            asyncio.run(router.chat_async([{"role": "user", "content": "hi"}]))

    def test_embed_raises_when_litellm_fails(self):
        router = ModelRouter()
        with pytest.raises(LiteLLMError):
            router.embed("hello world")

    def test_embed_async_raises_when_litellm_fails(self):
        router = ModelRouter()
        with pytest.raises(LiteLLMError):
            import asyncio

            asyncio.run(router.embed_async("hello world"))

    def test_retry_config_defaults(self):
        router = ModelRouter()
        config = router.router_config
        assert config.get("max_retries", 3) == 3
        assert config.get("retry_delay", 1.0) == 1.0
        assert config.get("fallbacks", []) == []

    def test_normalize_response_without_tool_calls(self):
        router = ModelRouter()

        class MockChoice:
            class Message:
                role = "assistant"
                content = "Hello!"
                tool_calls = None

            message = Message()

        class MockUsage:
            prompt_tokens = 10
            completion_tokens = 20
            total_tokens = 30

        class MockResponse:
            choices = [MockChoice()]
            model = "gpt-4o"
            usage = MockUsage()

        result = router._normalize_response(MockResponse())
        assert result["role"] == "assistant"
        assert result["content"] == "Hello!"
        assert result["model"] == "gpt-4o"
        assert result["usage"]["total_tokens"] == 30
        assert "tool_calls" not in result

    def test_normalize_response_with_tool_calls(self):
        router = ModelRouter()

        class MockFunction:
            name = "test_tool"
            arguments = '{"key": "value"}'

        class MockToolCall:
            id = "call_123"
            type = "function"
            function = MockFunction()

        class MockMessage:
            role = "assistant"
            content = ""
            tool_calls = [MockToolCall()]

        class MockChoice:
            message = MockMessage()

        class MockUsage:
            prompt_tokens = 10
            completion_tokens = 20
            total_tokens = 30

        class MockResponse:
            choices = [MockChoice()]
            model = "gpt-4o"
            usage = MockUsage()

        result = router._normalize_response(MockResponse())
        assert result["role"] == "assistant"
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["id"] == "call_123"
        assert result["tool_calls"][0]["function"]["name"] == "test_tool"
