# Context Assembly API

```python
from gitlord import ContextAssembler
```

Builds context for LLM calls from session history with dedup, summarization, and token budgeting.

## Constructor

```python
ContextAssembler(
    log_repo: GitRepo,
    dedup_enabled: bool = True,
    summarize_enabled: bool = True,
)
```

## Methods

### `assemble(branch, up_to_turn=None, budget_tokens=100000, rag_results=None) -> list[dict]`

Build the context message array for an LLM call.

```python
assembler = ContextAssembler(session.log_repo)
messages = assembler.assemble(
    branch="refs/agents/my-session",
    up_to_turn=50,
    budget_tokens=80000,
)
```

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `branch` | `str` | required | Branch ref to assemble from |
| `up_to_turn` | `int \| None` | `None` | Include turns up to this number (all if None) |
| `budget_tokens` | `int` | `100000` | Max tokens in assembled context |
| `rag_results` | `list \| None` | `None` | ChromaDB results to prepend as system message |

**Returns:** List of OpenAI-format message dicts

---

### `invalidate(branch) -> None`

Clear the context cache for a branch (e.g., after rewind).

---

### `compute_summary(branch, start_sha, end_sha, summary_content) -> Turn`

Create a summary turn that references a range of original turns.

```python
summary = assembler.compute_summary(
    branch="refs/agents/my-session",
    start_sha="abc123",
    end_sha="def456",
    summary_content="Summary of the conversation so far...",
)
session.append_turn(summary)
```

**Returns:** `Turn` with `role=TurnRole.summary` and `summarizes` field set

## Deduplication

File-read dedup applies to `tool_call`/`tool_result` turns where the tool reads a file path:

1. Compute content hash of the file read
2. Compare against most recent read of same path on the same branch
3. If hash matches → replace with `"[see turn N — content unchanged]"`
4. If hash differs → include full content

Dedup index is rebuilt lazily from git log on first access per session.

## Summarization

Summary turns have `role=TurnRole.summary` and a `summarizes` field listing SHAs of original turns they replace. During context assembly, summarized turns are substituted — the originals are skipped but remain queryable via `git show`.

## Caching

Context is cached per `(branch, turn N)`. On cache hit, only new turns since N are processed. Cache is invalidated on rewind or non-incremental writes.
