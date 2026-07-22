# Schemas Reference

All Pydantic models used across the framework. Import from `gitlord`.

```python
from gitlord import Turn, TurnRole, TurnError, CommitTrailers
from gitlord import SessionConfig, AgentConfig, MCPServerConfig, ChunkingConfig
from gitlord import GitlordError
```

---

## `GitlordError`

```python
class GitlordError(Exception):
    pass
```

Base exception for all GitLord errors. Subclasses: `GitError`, `CASError`, `LiteLLMError`, `ChromaDBError`.

---

## `TurnRole`

```python
class TurnRole(str, Enum):
    system = "system"
    user = "user"
    assistant = "assistant"
    tool_call = "tool_call"
    tool_result = "tool_result"
    summary = "summary"
```

Enum of valid turn roles. `summary` is used for compressed context turns.

---

## `TurnError`

```python
class TurnError(str, Enum):
    tool_call_error = "tool_call_error"
    timeout = "timeout"
```

---

## `Turn`

```python
class Turn(BaseModel):
    version: int = Field(default=1, ge=1)
    turn: int = Field(ge=0)
    turn_id: str = ""
    role: TurnRole
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent_id: str
    parent_agent_id: Optional[str] = None
    content: str = ""
    tool_name: Optional[str] = None
    tool_input: Optional[dict[str, Any]] = None
    tool_output: Optional[Any] = None
    model: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    error: Optional[str] = None
    workspace_commit: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    summarizes: Optional[list[str]] = None
```

| Field | Type | Description |
|-------|------|-------------|
| `version` | `int` | Schema version (always 1) |
| `turn` | `int` | Turn number (monotonic per branch, starting at 0) |
| `turn_id` | `str` | Unique turn identifier |
| `role` | `TurnRole` | Turn role |
| `timestamp` | `datetime` | UTC timestamp |
| `agent_id` | `str` | Full branch path of the agent that produced this turn |
| `parent_agent_id` | `str \| None` | Branch path one level up, or `None` for root session |
| `content` | `str` | Turn content (text) |
| `tool_name` | `str \| None` | Tool name (populated for `tool_call`/`tool_result` roles) |
| `tool_input` | `dict \| None` | Tool input arguments |
| `tool_output` | `Any \| None` | Tool output/result |
| `model` | `str \| None` | Model identifier used for this turn |
| `tokens_in` | `int` | Input tokens consumed |
| `tokens_out` | `int` | Output tokens produced |
| `cost` | `float` | Cost in USD |
| `error` | `str \| None` | Error message if turn failed |
| `workspace_commit` | `str \| None` | Workspace repo SHA if this turn mutated files |
| `tags` | `list[str]` | Free-form tags (e.g., `"err"`, `"retry"`) |
| `summarizes` | `list[str] \| None` | SHAs of turns this summary replaces (only for `role=summary`) |

---

## `CommitTrailers`

```python
class CommitTrailers(BaseModel):
    turn: int
    turn_id: str = ""
    role: str
    agent_id: str
    parent_agent_id: Optional[str] = None
    tool: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    error: Optional[str] = None
    workspace_commit: Optional[str] = None
    subagent_result: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    tool_calls: Optional[list[dict[str, Any]]] = None
    turn_tokens: int = 0
    parent_sha: Optional[str] = None
    subagent_id: Optional[str] = None
```

Parsed from git commit trailers. Used by `GitRepo.parse_trailers()`.

---

## `SessionConfig`

See [Configuration](../configuration.md).

---

## `AgentConfig`

See [Configuration](../configuration.md).

---

## `MCPServerConfig`

```python
class MCPServerConfig(BaseModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: Optional[dict[str, str]] = None
```

---

## `ChunkingConfig`

```python
class ChunkingConfig(BaseModel):
    chunk_size: int = 512
    chunk_overlap: int = 50
```
