# Index API

```python
from gitlord import IndexBuilder
```

Rebuilds the JSON index and vector index from git log.

## Constructor

```python
IndexBuilder(log_repo: GitRepo, vector_index: VectorIndex | None = None)
```

## Methods

### `rebuild_json_index() -> dict`

Walk all refs under `refs/agents/`, parse commit trailers, and build the JSON index.

```python
builder = IndexBuilder(session.log_repo)
index_data = builder.rebuild_json_index()
```

**Returns:** Index dict in the format:

```json
{
  "sessions": {
    "<session-id>": {
      "branch": "refs/agents/<session-id>",
      "turns": [
        {"sha": "7c1e4a2", "turn": 47, "role": "tool_call", "tags": ["err"], "tool": "fetch"}
      ],
      "subagents": ["<session-id>/<subagent-id>"]
    }
  }
}
```

---

### `rebuild_vector_index() -> int`

Clear the vector index and re-index all turns from git log.

```python
chunk_count = builder.rebuild_vector_index()
```

**Returns:** Total number of chunks indexed

---

### `to_file(path=None) -> dict`

Rebuild and write the JSON index to disk.

```python
index_data = builder.to_file()  # writes to .gitlord/index.json
index_data = builder.to_file("/custom/path/index.json")
```

**Returns:** The index dict

## Index Format

The JSON index is **rebuildable** — it's never the source of truth. Deleting it and rebuilding from git produces an identical result.

| Field | Type | Description |
|-------|------|-------------|
| `sessions` | `dict` | Map of session ID → session metadata |
| `sessions[id].branch` | `str` | Branch ref path |
| `sessions[id].turns` | `list` | Turn summaries (sha, turn, role, tags, tool) |
| `sessions[id].subagents` | `list[str]` | Subagent branch paths |

## Auto-Index

When `auto_index=True` (default), the index is rebuilt on every `Session.append_turn()`. Disable for performance in write-heavy scenarios.
