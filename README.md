# GitLord

**Git for AI Agents.**

GitLord turns Git into a database for autonomous agents. Every agent action becomes a version-controlled event — inspectable, replayable, forkable.

---

## Why GitLord?

AI agents are powerful but untrustworthy. When something goes wrong, you can't answer basic questions:

- Why did the agent make that decision?
- Which tool call caused the failure?
- Can we reproduce the execution?
- Can we branch from an earlier point?

Most frameworks lose this information. GitLord makes execution history a first-class primitive.

---

## How It Works

Every turn is a Git commit on a branch under `refs/agents/`:

```
refs/agents/my-agent
  commit 001 → user prompt
  commit 002 → assistant response
  commit 003 → tool call
  commit 004 → tool result
  commit 005 → subagent result
```

Git's primitives become agent primitives:

| Git Concept | Agent Concept |
|-------------|---------------|
| commits | agent events |
| branches | agent timelines |
| objects | durable state |
| history | memory |
| diffs | behavior changes |
| refs | agent identity |

---

## Features

### Inspect Everything

See exactly what an agent did, turn by turn. Every tool call, every decision, every failure — all version-controlled.

### Rewind Failures

Failed execution doesn't destroy history. Rewind to any checkpoint and try a different approach.

```
A --- B --- C --- D (failed)
       \
        E --- F (new attempt)
```

### Fork Alternative Approaches

Explore multiple solutions in parallel. Each branch maintains its own history.

### Track Subagents

Subagents run on their own branches with isolated histories. Results flow back to parents via Git trailers.

### Searchable History

Query past executions by turn, agent, tokens, errors, or semantic similarity. Your agents don't just remember documents — they remember experiences.

### Two-Repo Architecture

Clean separation between memory and workspace:

- **Log repo** — turn history, tool calls, metadata (no working-tree checkout needed)
- **Workspace repo** — actual files the agent reads/writes

---

## Install

```bash
pip install gitlord           # core (just pydantic)
pip install gitlord[all]      # everything
pip install gitlord[mcp]      # MCP server support
pip install gitlord[litellm]  # LLM model routing
pip install gitlord[chromadb] # vector index for RAG
```

---

## Quickstart

```python
from gitlord import Session, SessionConfig, Turn, TurnRole

config = SessionConfig(log_repo_path="log")
session = Session.create("my-agent", config)
session.append_user_turn("Hello, what's the weather in London?")

turns = session.get_turns()
for t in turns:
    print(f"  [{t.role}] {t.content[:80]}")
```

---

## CLI

```bash
gitlord run my-session                    # run a session
gitlord log my-session                    # view turn history
gitlord tree my-session                   # view branch structure
gitlord show <sha>                        # show turn JSON
gitlord rewind my-session <sha>           # rewind to checkpoint
gitlord diff <sha-a> <sha-b>              # diff two turns
gitlord index                             # rebuild search index
gitlord trim my-session [N]              # trim to N turns
```

---

## Architecture

| Module | What It Does |
|--------|--------------|
| `gitlord.git` | Git plumbing — tree/commit construction, CAS updates, orphan branches |
| `gitlord.session` | Session lifecycle — create, resume, append turns, rewind |
| `gitlord.subagent` | Subagent management — spawn, complete, drain, trim |
| `gitlord.context` | Context assembly — dedup, summarization, token budget |
| `gitlord.mcp` | MCP server lifecycle — tool discovery, crash recovery |
| `gitlord.model` | LLM router — tool schema translation, retry/fallback |
| `gitlord.rag` | Vector index — ChromaDB wrapper, MMR search |
| `gitlord.index` | JSON index — rebuild from git log |
| `gitlord.query` | In-memory query layer — filter, group, aggregate |
| `gitlord.cli` | CLI — run, log, tree, show, rewind, diff, index, trim |

---

## The Vision

Software has Git. Documents have version history. Code has commits.

AI agents should have the same thing.

GitLord is a version control system for autonomous intelligence.

---

## License

MIT
