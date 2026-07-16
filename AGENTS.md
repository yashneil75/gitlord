# AGENTS.md

## Framework: GitLord — Agent Orchestration with Git-Backed Storage

### What was done
- Full implementation of all 7 tasks from PLAN.md via subagent-driven development
- 165 tests (154 pass, 11 skipped for optional chromadb) — all modules fully implemented
- Review found 2 Critical issues, both fixed

### File Map

| File | Purpose | Spec § |
|------|---------|--------|
| `gitlord/__init__.py` | Package exports | — |
| `gitlord/schemas.py` | Pydantic models: Turn, CommitTrailers, MCPServerConfig, etc. | §3 |
| `gitlord/config.py` | Config loading from file/env | — |
| `gitlord/git.py` | Git plumbing (hash-object, mktree, commit-tree, update-ref) | §4.4 |
| `gitlord/session.py` | Session lifecycle (create, turn append, rewind) | §4.1–4.2, §9.2 |
| `gitlord/subagent.py` | Subagent spawn/completion, queue management, trim | §4.3 |
| `gitlord/context.py` | Context assembly pipeline, dedup, summarization, cache | §6 |
| `gitlord/mcp.py` | MCPMon server lifecycle manager | §7 |
| `gitlord/model.py` | LiteLLM router, tool schema translator | §8 |
| `gitlord/rag.py` | ChromaDB vector index wrapper | §5.2 |
| `gitlord/index.py` | JSON index rebuild from git log | §5.1 |
| `gitlord/cli.py` | Typer CLI (run, log, tree, show, rewind, diff, index, mcp add, trim) | §9 |
| `pyproject.toml` | Package definition with optional deps | — |

### Key Decisions
- **Subprocess git** over `isomorphic-git` or `gitpython` — matches spec's plumbing focus, zero extra deps for core
- **Optional imports** for litellm, chromadb, typer, ulid — core runs without them, features degrade gracefully
- **Thread-safe queue** in SubagentManager with per-parent queues, max depth 1 per active subagent per spec
- **Session._branch** is mutable for rewind (creates new branch on same Session class)
- **Subagent branch format**: `refs/agents/sub/<session-id>/<subagent-id>` — the `sub/` prefix avoids git's file/directory conflict (git won't allow `refs/agents/<session>` as both a loose ref and a prefix for `refs/agents/<session>/<sub>`)
- **CAS retry recalculates turn number** inside rebuild closure to avoid duplicate turn numbers on concurrent writes
- **MCP protocol** uses real JSON-RPC 2.0 over stdin/stdout with initialize+tools/list handshake

### Dependencies
- **Required:** pydantic>=2.0
- **Optional:** litellm, chromadb, typer, ulid-py
- Install all: `pip install gitlord[all]`

### Gotchas
- `Session._branch` is mutated by rewind — callers must use the returned Session, not the original
- SubagentManager `_get_depth` normalizes out the `sub/` prefix: root session = depth 0, first-level subagent = depth 1
- Context assembler uses heuristic token counting (len//4) — not accurate; needs real tokenizer
- `Session.rewind` does `rev_parse(target_sha)` to accept abbreviated SHAs — this was added to fix CLI rewind with log output
- CLI tests use `cwd_isolation` fixture (chdir to tmp_path) — tests must be serial; parallel execution would break due to shared CWD
- Turn number recalculation in CAS rebuild closure requires `_next_turn_number()` to be callable from inside `rebuild()` — it reads the current branch tip which is the `old_parent` at CAS retry time
- `get_turn_filename` returns the *last* matching turn file (sorted) — important because system turns and content turns coexist in the tree
- MCP tests use a mock server script (`tests/mock_mcp_server.py`) — real servers need the actual `uvx` or `npx` commands
- ChromaDB tests are skipped when `chromadb` not installed — 11 skipped tests in `test_rag.py`
