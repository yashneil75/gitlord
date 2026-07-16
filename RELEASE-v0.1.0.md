# v0.1.0 — Initial release

Agent orchestration framework with Git-backed storage. Every turn is a Git commit — inspectable, rewindable, forkable.

GitLord treats Git itself as the agent's state database. Sessions live as branches under `refs/agents/`, subagents run on their own branches, and `git log` is your conversation history.

## Install + verify

```bash
pip install gitlord==0.1.0             # verify this exact version
pip install gitlord[all]               # with all optional extras
gitlord --help                          # CLI entry point
```

## Architecture

| Module | Responsibility |
|--------|----------------|
| `gitlord.git` | Git plumbing — tree/commit construction, CAS updates, orphan branches |
| `gitlord.session` | Session lifecycle — create, resume, append turns, rewind |
| `gitlord.subagent` | Subagent management — spawn, complete, drain, trim |
| `gitlord.context` | Context assembly — dedup, summarization, token budget, cache |
| `gitlord.mcp` | MCP server lifecycle — tool discovery, call, crash recovery (official `mcp` SDK) |
| `gitlord.model` | LiteLLM router — tool schema translation, retry/fallback |
| `gitlord.rag` | Vector index — ChromaDB wrapper, MMR search |
| `gitlord.index` | JSON index — rebuild from git log |
| `gitlord.cli` | Typer CLI — `gitlord run`, `log`, `tree`, `show`, `rewind`, `diff`, `index`, `trim` |

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

## CLI

```bash
gitlord run     my-session          # run a session
gitlord log     my-session          # view turn history
gitlord tree    my-session          # view branch structure
gitlord show    <sha>               # show turn JSON
gitlord rewind  my-session <sha>    # rewind to checkpoint
gitlord diff    <sha-a> <sha-b>     # diff two turns
gitlord index                       # rebuild JSON index from git log
gitlord trim    my-session [N]      # trim session to N turns
```

## Optional extras

```bash
pip install gitlord[mcp]             # MCP tool integration (official mcp SDK)
pip install gitlord[litellm]         # LLM model routing
pip install gitlord[chromadb]        # ChromaDB vector index for RAG
pip install gitlord[cli]             # Typer CLI
pip install gitlord[ulid]            # ULID session IDs
```

The core package only requires `pydantic>=2.0`; everything else is opt-in. Compatible with Python 3.11+.

## Quality

- **179 tests passing** (no skips, ~3 min on a typical machine)
- Full type annotations across all public methods
- Optional imports — `litellm`, `chromadb`, `typer`, `mcp`, `tiktoken`, `ulid` all degrade gracefully
- CAS-based concurrency with retry/rebuild for branch updates
- Thread-safe subagent queues with bounded depth
- MCP transport uses the official `mcp` Python SDK (1.27+)

## Verify this exact release

```bash
pip install gitlord==0.1.0 && python -c "import gitlord; print(gitlord.__version__)"
# Expected output: 0.1.0
```

---

Built by [@yashneil75](https://github.com/yashneil75). MIT licensed. Issues welcome.
