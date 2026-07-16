# GitLord

Agent orchestration framework with Git-backed storage. Every turn is a Git commit — inspectable, rewindable, forkable.

## Quickstart

```bash
pip install gitlord[all]
```

```python
from gitlord import Session, SessionConfig, Turn, TurnRole

config = SessionConfig(log_repo_path="log")
session = Session.create("my-agent", config)
session.append_user_turn("Hello, what's the weather in London?")

turns = session.get_turns()
for t in turns:
    print(f"  [{t.role}] {t.content[:80]}")
```

## Install

```
pip install gitlord           # core only (pydantic)
pip install gitlord[all]      # everything
pip install gitlord[mcp]      # MCP server support
pip install gitlord[llm]      # LLM model routing (litellm)
pip install gitlord[rag]      # ChromaDB vector index
```

## Architecture

| Module | What |
|--------|------|
| `gitlord.git` | Git plumbing — tree/commit construction, CAS updates, orphan branches |
| `gitlord.session` | Session lifecycle — create, resume, append turns, rewind |
| `gitlord.subagent` | Subagent management — spawn, complete, drain, trim |
| `gitlord.context` | Context assembly — dedup, summarization, token budget, cache |
| `gitlord.mcp` | MCP server lifecycle — tool discovery, call, crash recovery |
| `gitlord.model` | LLM router — tool schema translation, retry/fallback |
| `gitlord.rag` | Vector index — ChromaDB wrapper, MMR search |
| `gitlord.index` | JSON index — rebuild from git log |
| `gitlord.cli` | CLI — `gitlord run`, `log`, `tree`, `show`, `rewind`, `diff`, `index` |

## How it works

Every turn is a Git commit on a branch under `refs/agents/`:
- `refs/agents/<session-id>` — session branch
- `refs/agents/sub/<session-id>/<ulid>` — subagent branches

Turn content is stored as JSON blobs in a `turns/` subdirectory in the tree.
Git trailers encode turn metadata (turn number, type, agent, tokens).
Subagent results are linked via `Subagent-Result` trailer.

## CLI

```bash
gitlord run my-session                    # run a session
gitlord log my-session                    # view turn history
gitlord tree my-session                   # view branch structure
gitlord show <sha>                        # show turn JSON
gitlord rewind my-session <sha>           # rewind to checkpoint
gitlord diff <sha-a> <sha-b>              # diff two turns
```

## Development

```bash
pip install -e ".[all,dev]"
pytest tests/
```

## License

MIT
