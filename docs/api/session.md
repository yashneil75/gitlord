# Session API

```python
from gitlord import Session, SessionConfig
```

The `Session` class manages the full lifecycle of an agent session: creation, turn appending, rewinding, and querying.

## Constructor

```python
Session(log_repo: GitRepo, workspace_repo: GitRepo, config: SessionConfig, session_id: str)
```

Internal constructor. Use `Session.create()` or `Session.resume()` instead.

## Properties

| Property | Type | Description |
|----------|------|-------------|
| `session_id` | `str` | The session's ULID identifier |
| `branch` | `str` | Git ref path (e.g., `refs/agents/<session-id>`) |
| `config` | `SessionConfig` | Session configuration |
| `log_repo` | `GitRepo` | The log repository handle |
| `workspace_repo` | `GitRepo` | The workspace repository handle |

## Class Methods

### `Session.create(session_id, config) -> Session`

Create a new session. Generates an orphan git branch and commits the initial system turn.

```python
config = SessionConfig(log_repo_path="log")
session = Session.create("my-agent", config)
```

**Raises:**
- `ValueError` — if `session_id` is invalid or already exists

---

### `Session.resume(session_id, config) -> Session`

Resume an existing session. Verifies the branch exists.

```python
session = Session.resume("my-agent", config)
```

**Raises:**
- `ValueError` — if session branch not found

## Turn Methods

### `append_turn(turn) -> str`

Append a pre-built `Turn` object. Sets timestamp and turn number automatically.

```python
from gitlord import Turn, TurnRole

turn = Turn(
    role=TurnRole.user,
    content="Hello, world!",
    agent_id=session.session_id,
)
sha = session.append_turn(turn)
```

**Returns:** Commit SHA

---

### `append_user_turn(content, tags=None) -> str`

Convenience method for appending a user turn.

```python
sha = session.append_user_turn("What's the weather?")
```

---

### `append_assistant_turn(content, model, tokens_in=0, tokens_out=0, tags=None, cost=0.0) -> str`

Convenience method for appending an assistant turn.

```python
sha = session.append_assistant_turn(
    content="It's sunny today.",
    model="gpt-4o",
    tokens_in=12,
    tokens_out=8,
)
```

---

### `append_tool_call_turn(tool_name, tool_input, tags=None) -> str`

Append a tool call turn.

```python
sha = session.append_tool_call_turn(
    tool_name="fetch",
    tool_input={"url": "https://api.weather.com"},
)
```

---

### `append_tool_result_turn(tool_name, tool_output, tags=None) -> str`

Append a tool result turn.

```python
sha = session.append_tool_result_turn(
    tool_name="fetch",
    tool_output='{"temp": 15}',
)
```

---

### `append_summary_turn(content, summarizes, model, tokens_in=0, tokens_out=0) -> str`

Append a summary turn that references original turns.

```python
sha = session.append_summary_turn(
    content="Summary of turns 0-10...",
    summarizes=["sha1", "sha2", "sha3"],
    model="gpt-4o",
)
```

## Query Methods

### `get_turns(start=0, end=None) -> list[Turn]`

Walk branch commits and return turn objects.

```python
turns = session.get_turns()           # all turns
turns = session.get_turns(start=5)    # turns 5+
turns = session.get_turns(end=10)     # turns 0-10
```

---

### `get_turn_count() -> int`

Return the next turn number (total turns committed).

---

### `get_branch_path() -> str`

Return the full git ref path for this session's branch.

---

### `query() -> TurnQuery`

Return an in-memory query interface for the session's turns. See [Query API](./query.md).

```python
results = session.query().where(role="assistant").sum("tokens_out")
```

---

### `snapshot(up_to_turn, output_path=None) -> int`

Compress turns up to a given number into a snapshot JSON file.

```python
count = session.snapshot(up_to_turn=50)
```

**Returns:** Number of turns compressed.

---

### `rebuild_index() -> None`

Force a rebuild of the JSON index from git log.

## Rewind

### `rewind(target_sha, branch_name=None) -> Session`

Create a new branch at `target_sha`. The original branch and all turns after the target remain intact.

```python
new_session = session.rewind("abc123")
new_session = session.rewind("abc123", branch_name="refs/agents/experiment-v2")
```

**Parameters:**
- `target_sha` — Commit SHA to rewind to (supports abbreviated SHAs)
- `branch_name` — Custom name for the new branch (auto-generated if None)

**Returns:** New `Session` pointing at the new branch

**Raises:**
- `ValueError` — if commit not found, not on branch, or target branch already exists

**Behavior:**
- Creates branch `<branch>-rewind-<short-sha>` if no name given
- If target turn has a `workspace_commit`, checks out that workspace state
- Never mutates or deletes existing refs
