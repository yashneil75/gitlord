# Subagent API

```python
from gitlord import SubagentManager
```

Manages subagent lifecycle: spawning, completion, queue draining, and branch cleanup.

## Constructor

```python
SubagentManager(
    log_repo: GitRepo,
    workspace_repo: GitRepo,
    config: SessionConfig,
    session_id: str,
)
```

## Methods

### `spawn(parent_branch, parent_agent_id) -> tuple[str, str]`

Spawn a new subagent. Creates a branch at the parent's current tip.

```python
subagent_id, branch = manager.spawn(
    parent_branch="refs/agents/my-session",
    parent_agent_id="my-session",
)
```

**Returns:** `(subagent_id, branch_ref)`

**Raises:**
- `ValueError` — if max depth exceeded

---

### `complete(subagent_id, final_sha) -> None`

Mark a subagent as complete. Enqueues its result for parent integration.

```python
manager.complete(subagent_id, final_commit_sha)
```

---

### `drain_queue(parent_branch) -> int`

Process all pending subagent results for a parent branch. Appends one turn per result with the `Subagent-Result` trailer.

```python
count = manager.drain_queue("refs/agents/my-session")
```

**Returns:** Number of results processed

---

### `trim(session_id=None, all_sessions=False, keep_active=True) -> int`

Delete completed subagent branches.

```python
# Trim one session
count = manager.trim(session_id="my-session")

# Trim all sessions
count = manager.trim(all_sessions=True)
```

**Returns:** Number of branches deleted

**Behavior:**
- Skips root session branches (depth ≤ 1)
- Skips active (not yet completed) subagents by default
- Deletes branches and records cleanup in git reflog

---

### `get_depth(branch) -> int`

Get the nesting depth of a branch.

```python
depth = manager.get_depth("refs/agents/sub/my-session/sub-001")  # → 1
```

---

### `list_active() -> dict`

Return a dict of active subagents with their metadata.

## Thread Safety

All public methods are protected by `threading.Lock`. Subagent completions are enqueued, not applied immediately — the parent processes them serially after its current turn.

## Branch Format

```
refs/agents/sub/<session-id>/<subagent-id>
```

The `sub/` prefix avoids git's file/directory naming conflict.
