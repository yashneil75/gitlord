# GitLord Documentation

Agent orchestration framework with git-backed storage. Every turn is a Git commit: inspectable, rewindable, forkable.

## Contents

### Getting Started

- [Installation](./getting-started.md#installation)
- [Quickstart](./getting-started.md#quickstart)
- [Configuration](./configuration.md)

### Architecture

- [Overview](./architecture.md) — Two-repo model, ref namespace, commit construction
- [How It Works](./architecture.md#how-it-works) — Turn lifecycle, subagent flow, context assembly

### API Reference

| Module | Description |
|--------|-------------|
| [Schemas](./api/schemas.md) | Pydantic models: `Turn`, `TurnRole`, `SessionConfig`, `CommitTrailers`, etc. |
| [Session](./api/session.md) | `Session` — create, resume, append turns, rewind, query, snapshot |
| [Git](./api/git.md) | `GitRepo` — git plumbing: hash-object, mktree, commit-tree, CAS updates |
| [Subagent](./api/subagent.md) | `SubagentManager` — spawn, complete, drain queue, trim |
| [Context](./api/context.md) | `ContextAssembler` — dedup, summarization, token budget, cache |
| [MCP](./api/mcp.md) | `MCPMon` — MCP server lifecycle, tool discovery, crash recovery |
| [Model](./api/model.md) | `ModelRouter` — LiteLLM routing, tool schema translation |
| [RAG](./api/rag.md) | `VectorIndex` — ChromaDB wrapper, MMR search, chunking |
| [Index](./api/index.md) | `IndexBuilder` — JSON index rebuild from git log |
| [Query](./api/query.md) | `TurnQuery` — in-memory query layer with aggregations |
| [CLI](./api/cli.md) | `gitlord` CLI commands reference |
| [Config](./api/config.md) | `Config` — loading from file, env, dict |

### Guides

- [Subagent Orchestration](./guides/subagents.md) — Spawning, completing, trimming subagents
- [Context Management](./guides/context.md) — Dedup, summarization, token budgets
- [MCP Integration](./guides/mcp.md) — Adding and managing MCP tool servers
- [Rewind & Fork](./guides/rewind.md) — Rewinding sessions and forking history
