# AGENTS.md

## Framework: GitLord — Agent Orchestration with Git-Backed Storage

### What was done
- Scaffolded the full gitlord package from SPEC.md (12 modules + pyproject.toml)
- All modules import cleanly
- Task 6: Index rebuild + CLI tests, added `GitRepo.rev_parse()` for SHA resolution

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

### Dependencies
- **Required:** pydantic>=2.0
- **Optional:** litellm, chromadb, typer, ulid-py
- Install all: `pip install gitlord[all]`

### Gotchas
- `Session._branch` is mutated by rewind — callers must use the returned Session, not the original
- SubagentManager trim skips branches with depth <= 3 (root) and active subagents
- Context assembler uses heuristic token counting (len//4) — not accurate; needs real tokenizer
- MCPMon tool discovery is stubbed — needs actual MCP protocol handshake for real servers
- `Session.rewind` does `rev_parse(target_sha)` to accept abbreviated SHAs — this was added to fix CLI rewind with log output
- CLI tests use `cwd_isolation` fixture (chdir to tmp_path) — tests must be serial; parallel execution would break due to shared CWD
