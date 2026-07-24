# v0.1.0 — Initial release

**Git for AI Agents.**

GitLord turns Git into a database for autonomous agents. Every agent action becomes a version-controlled event — inspectable, replayable, forkable.

## Install

```bash
pip install gitlord==0.1.0
pip install gitlord[all]      # everything
gitlord --help
```

## What's Inside

- **Git as database** — sessions, turns, subagents all live as branches under `refs/agents/`
- **Full history** — every tool call, decision, and failure is a commit you can inspect
- **Rewind & fork** — failed execution? rewind to any checkpoint, try a different path
- **Subagent tracking** — isolated branches per subagent, results linked via trailers
- **Searchable history** — JSON index + vector index for semantic retrieval
- **Two-repo architecture** — clean separation between memory and workspace
- **MCP integration** — official `mcp` SDK for tool access
- **Model routing** — LiteLLM for multi-model support with retry/fallback
- **Context management** — dedup, summarization, token budgeting

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
gitlord index                       # rebuild search index
gitlord trim    my-session [N]      # trim to N turns
```

## Quality

- **208 tests passing** — all modules fully covered
- Full type annotations across all public methods
- Optional imports degrade gracefully (litellm, chromadb, typer, mcp, tiktoken, ulid)
- CAS-based concurrency with retry/rebuild for branch updates
- Thread-safe subagent queues with bounded depth
- Core requires only `pydantic>=2.0` — everything else is opt-in
- Python 3.11+

## Verify

```bash
pip install gitlord==0.1.0 && python -c "import gitlord; print(gitlord.__version__)"
```

## License

MIT
