# Model Router API

```python
from gitlord import ModelRouter
```

Routes LLM calls through LiteLLM with provider-specific schema translation.

## Constructor

```python
ModelRouter(model: str = "gpt-4o", provider_params: dict | None = None)
```

## Methods

### `chat_completion(messages, tools=None, **kwargs) -> dict`

Synchronous chat completion.

```python
router = ModelRouter("gpt-4o")
response = router.chat_completion(
    messages=[{"role": "user", "content": "Hello"}],
    tools=[{"type": "function", "function": {"name": "get_weather", ...}}],
)
```

**Returns:** LiteLLM response dict with `choices`, `usage`, etc.

---

### `async_chat_completion(messages, tools=None, **kwargs) -> dict`

Async version of `chat_completion`.

---

### `embed(texts, model=None) -> list[list[float]]`

Generate embeddings for a list of texts.

---

### `translate_tools(tools, provider=None) -> list[dict]`

Translate internal tool schemas to provider-specific format.

```python
openai_tools = router.translate_tools(internal_tools, provider="openai")
anthropic_tools = router.translate_tools(internal_tools, provider="anthropic")
```

**Supported providers:**
| Provider | Format |
|----------|--------|
| `openai` | `{"type":"function","function":{"name","description","parameters"}}` |
| `anthropic` | `{"name","description","input_schema"}` |
| `google` | `{"function_declarations":[{"name","description","parameters"}]}` |

---

### `infer_provider(model) -> str`

Infer the provider from a model name string.

```python
router.infer_provider("gpt-4o")           # → "openai"
router.infer_provider("claude-sonnet-5")  # → "anthropic"
router.infer_provider("gemini-2.0-flash") # → "google"
```

## Error Handling

- `LiteLLMError` raised on provider errors
- Automatic retry with configurable backoff
- Graceful degradation when `litellm` is not installed (methods raise `ImportError`)
