# Context Management

GitLord assembles context for LLM calls from session history, with deduplication, summarization, and token budgeting.

## Basic Usage

```python
from gitlord import ContextAssembler

assembler = ContextAssembler(session.log_repo)
messages = assembler.assemble(
    branch=session.branch,
    up_to_turn=50,
    budget_tokens=80000,
)
```

## How It Works

1. **Cache check** — If context for `(branch, turn N)` exists, only new turns are processed
2. **Walk history** — Traverse commits from root to `up_to_turn`
3. **Apply summaries** — Substitute summarized turns (identified by `summarizes` field)
4. **Apply dedup** — Replace repeated file reads with same content hash
5. **Enforce budget** — Walk newest-first, accumulate tokens, stop when budget exceeded
6. **Prepend RAG** — Add ChromaDB results as system message if provided
7. **Cache** — Store result for future incremental builds

## Deduplication

When an agent reads the same file multiple times in a session:

1. Compute content hash of the file
2. Compare against the most recent read of the same path on the same branch
3. If hash matches → replace with `"[see turn N — content unchanged]"`
4. If hash differs (file was modified) → include full content

```python
assembler = ContextAssembler(session.log_repo, dedup_enabled=True)
```

## Summarization

Create summary turns to compress long conversations:

```python
summary = assembler.compute_summary(
    branch=session.branch,
    start_sha="abc123",
    end_sha="def456",
    summary_content="The user asked about weather, we fetched data, and provided a forecast.",
)
session.append_turn(summary)
```

Summary turns have `role=TurnRole.summary` and a `summarizes` field listing the SHAs they replace. Original turns remain in git history but are skipped during context assembly.

## Token Budgeting

Context assembly enforces a token budget (default: 100,000 tokens):

```python
messages = assembler.assemble(
    branch=session.branch,
    budget_tokens=50000,  # smaller budget
)
```

The assembler walks from newest to oldest turn, accumulating token counts (estimated via `len(content) // 4`), and stops when the budget is exceeded.

## Invalidation

Clear the cache after a rewind or external modification:

```python
assembler.invalidate(session.branch)
```
