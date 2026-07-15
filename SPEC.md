# SPEC.md

## 1. Overview

Agent orchestration framework using a git repository as the storage backend for agent execution history. Every session, subagent, and turn is represented as git objects. A vector index (ChromaDB) is derived from the git log for retrieval. Model calls are routed through LiteLLM. Tool access is provided via bundled MCP servers.

---

## 2. Repository Structure

### 2.1 Ref Namespace

```
refs/agents/<session-id>                          # root session branch
refs/agents/<session-id>/<subagent-id>             # subagent branch
refs/agents/<session-id>/<subagent-id>/<subagent-id>  # nested subagent branch (arbitrary depth)
```

- `session-id`: ULID generated at session start.
- `subagent-id`: ULID generated at subagent spawn, scoped under parent path.
- Branch depth is capped at a configurable limit (default: 8). The framework enforces this at subagent spawn time; spawning beyond the cap is rejected with an error. Each level is a literal path segment.

### 2.2 Working Tree Layout (per branch)

```
  turns/
  00000000000000000000-system.json
  00000000000000000001-user.json
  00000000000000000002-assistant.json
  00000000000000000003-tool_call.json
  00000000000000000004-tool_result.json
  ...
index.json        # generated, not committed (see 5.1)
```

- One file per turn. Filename: zero-padded 20-digit turn number, hyphen, role.
- Turn numbers are monotonic per branch, starting at 0.
- Files are JSON. Schema in 3.1.

### 2.3 Two-Repo Model

Two separate git repositories:

1. **Log repo** — stores turn history as described above. No working-tree checkouts required for writes; commits are constructed via plumbing.
2. **Workspace repo** — stores actual files an agent reads/writes/edits (the agent's working project directory). Uses standard working-tree commits. Subagents that need isolated concurrent file access use `git worktree add` against this repo, one worktree per subagent branch.

The two repos are not merged. A log turn that corresponds to a workspace mutation stores the workspace repo's commit SHA in its turn JSON (`workspace_commit` field, 3.1) as a cross-reference.

---

## 3. Data Model

### 3.1 Turn JSON Schema

```json
{
  "version": 1,
  "turn": 47,
  "role": "assistant | user | system | tool_call | tool_result",
  "timestamp": "2026-07-11T14:32:00Z",
  "agent_id": "session-id/subagent-id/...",
  "parent_agent_id": "session-id/subagent-id" ,
  "content": "...",
  "tool_name": null,
  "tool_input": null,
  "tool_output": null,
  "model": "claude-sonnet-5",
  "tokens_in": 812,
  "tokens_out": 214,
  "workspace_commit": null,
  "tags": []
}
```

- `agent_id`: full branch path of the agent that produced this turn.
- `parent_agent_id`: null for root session; otherwise the branch path one level up.
- `tool_name`/`tool_input`/`tool_output`: populated only for `tool_call`/`tool_result` roles, else null.
- `workspace_commit`: SHA in the workspace repo if this turn mutated files, else null.
- `tags`: free-form strings for indexing (e.g. `"err"`, `"retry"`).

### 3.2 Commit Message Format

```
[turn:<n>][role:<role>][tags:<comma-separated>] <one-line summary, <=72 chars>

Turn: <n>
Role: <role>
Agent: <agent_id>
Parent-Agent: <parent_agent_id or "none">
Tool: <tool_name or "none">
Tokens-In: <n>
Tokens-Out: <n>
Workspace-Commit: <sha or "none">
Subagent-Result: <sha, present only on the commit where a subagent's final turn is linked back into the parent branch>
```

- Header line (`[turn:...]`) is the parseable summary line for `git log --oneline` scanning and index rebuild.
- Trailer block (`Turn:` through `Subagent-Result:`) is machine-parsed key-value; one key per line, `Key: value` format, no wrapping.
- `Subagent-Result` trailer is the sole mechanism linking a subagent's completion into its parent's history (see 4.3). It is not a git merge.

---

## 4. Agent Lifecycle

### 4.1 Session Start

1. Generate `session-id`.
2. Create orphan branch `refs/agents/<session-id>`.
3. Write `turns/00000000000000000000-system.json`.
4. Commit via plumbing (4.4) with turn 0 trailers.

### 4.2 Turn Append

1. Determine next turn number (`last turn on branch + 1`).
2. Write `turns/NNNNNNNNNNNNNNNNNNNN-<role>.json` as a git blob (no working tree write required).
3. Construct a new tree object: prior tree + new blob at `turns/NNNNNNNNNNNNNNNNNNNN-<role>.json` → new tree SHA via `mktree`.
4. Create commit object: parent = current branch tip, tree = new tree, message = per 3.2.
5. Update ref (`update-ref refs/agents/<path> <new-commit-sha>`).

### 4.3 Subagent Spawn / Completion

**Spawn:**
1. Generate `subagent-id`.
2. Create branch `refs/agents/<parent-path>/<subagent-id>` pointing at the parent's current tip commit (branch point).
3. Subagent appends its own turns per 4.2 on its own branch.

**Completion:**
1. Subagent's final turn is committed on the subagent branch as normal.
2. The subagent's completion result is enqueued in the parent's append queue (a FIFO queue of pending subagent results, max depth 1 per active subagent).
3. The parent processes the queue serially: if the parent is mid-turn, the queued result waits until the current turn completes. Once the parent's current turn finishes, it drains the queue by appending one turn per queued result (4.2), each with the `Subagent-Result` trailer set to the subagent's final commit SHA.
4. After the queue is fully drained, the parent is notified (callback or event) that all pending subagent results have been integrated, and can proceed with its next action.
5. No merge commit is created. Subagent branches are deleted after the parent has integrated the result (the `Subagent-Result` trailer provides permanent traceability). If `--keep-subagent-branches` is set, branches are retained for debugging.

**Branch cleanup / trimming:**
- Completed subagent branches are deleted by default after parent integration. The trailer in the parent's turn preserves the link to the subagent's final commit SHA.
- `agent trim <session-id>` deletes all completed subagent branches for a session, keeping only the root branch and any active (not yet completed) subagent branches.
- `agent trim --all` trims all completed subagent branches across all sessions.
- Retained subagent branches (via `--keep-subagent-branches`) can be pruned later with `agent trim`.

### 4.4 Commit Construction (Plumbing, No Working Tree)

Sequence per turn commit (isomorphic-git or equivalent object-level API):

1. `hash-object` the turn JSON content → blob SHA.
2. `read-tree`/reconstruct current tree, insert new blob at `turns/NNNNNNNNNNNNNNNNNNNN-<role>.json` → new tree SHA via `mktree`.
3. `commit-tree <tree-sha> -p <parent-commit-sha> -m <message>` → commit SHA.
4. `update-ref refs/agents/<path> <commit-sha> <old-commit-sha>` (compare-and-swap to catch concurrent writers).

No `git add`, no working tree checkout, no index (staging area) usage. This is required for concurrent subagent writers appending to different branches without lock contention on a shared working directory.

### 4.5 Concurrency

- Log repo: plumbing-only commits (4.4) mean no working tree, so parallel subagents on different branches do not contend. Ref updates use compare-and-swap; on CAS failure, retry with re-read parent.
- Parent append queue: subagent completions are enqueued, not applied immediately. The parent processes completions serially, one at a time, after its current turn ends. This prevents write storms when multiple subagents complete simultaneously.
- Workspace repo: parallel subagents needing file access each get a `git worktree add <path> <branch>`. Worktrees are removed on subagent completion.

---

## 5. Indexing

### 5.1 JSON Index

- Rebuildable, not source of truth. Deleting it and rebuilding from git must produce an identical result.
- Build process: `git log --all --format=<trailer-parseable format>` across all refs under `refs/agents/`, parse trailers, emit structured JSON.

```json
{
  "sessions": {
    "<session-id>": {
      "branch": "refs/agents/<session-id>",
      "turns": [
        { "sha": "7c1e4a2", "turn": 47, "role": "tool_call", "tags": ["err"], "tool": "fetch" }
      ],
      "subagents": ["<session-id>/<subagent-id>"]
    }
  }
}
```

- Rebuild trigger: on demand (CLI command) or on a debounce timer after last write to a branch. Not rebuilt on every single commit by default (configurable).

### 5.2 Vector Index (ChromaDB)

The vector index is a thin wrapper around ChromaDB. ChromaDB handles storage, embedding, and querying internally — the framework does not reimplement any of this.

**Document types** (distinguished by metadata):

```json
{
  "type": "agent_turn",
  "sha": "7c1e4a2",
  "agent_id": "session-id/subagent-id",
  "turn": 47
}
```
```json
{
  "type": "rag_doc",
  "doc_id": "external-doc-id",
  "source": "file:///path or https://url"
}
```

**Chunking strategy:** The user defines what content gets sent to ChromaDB and how it is chunked. The framework provides defaults but the user can override:

- **Turn content chunking:** By default, each turn's `content` field is a single document. Users can configure chunking (e.g., by paragraph, by token count with overlap) for long turns via a `chunking` config option. ChromaDB's built-in chunking is used where applicable.
- **RAG document chunking:** The user specifies the chunking strategy at ingestion time (e.g., `chunk_size: 512, chunk_overlap: 50`). ChromaDB's native chunking handles splitting.
- **Embedding model:** Configurable, invoked via LiteLLM's embedding endpoint where supported, or a direct embedding provider call otherwise.

**Indexing:**
- `agent_turn` entries are indexed at commit time (turn content only, not full trailer block).
- `rag_doc` entries are indexed at ingestion time (see 6.3).

**Querying:** Returns `(id, type, score)`. Caller resolves `agent_turn` results via `git show <sha>:turns/NNNNNNNNNNNNNNNNNNNN-<role>.json` and `rag_doc` results via the external store/original file.

---

## 6. Context Management

### 6.1 Principle

All compression/cleaning happens at read time (context assembly for the next LLM call), never at write time. The log repo always contains full-fidelity, uncompressed turn data.

### 6.2 Deduplication (file reads)

- Applies only to tool calls that read a file path also present in the workspace repo.
- On a repeated read of the same path within the same branch lineage, compute the current file's content hash. If it matches the hash recorded on the most recent prior read of that path on the same lineage, the repeated read is elided from the assembled context and replaced with a reference to the earlier turn (`"see turn N"`).
- If the hash differs (file changed, possibly by another subagent), the read is included in full.
- A lightweight in-memory index maps `(branch, path) → most recent read turn + hash` for the active branch lineage. This avoids walking the full branch history on every dedup check. The index is rebuilt lazily from git log on first access per session, then maintained incrementally.

### 6.3 Summarization

- Triggered by context-window budget policy (configurable: turn count threshold, token threshold, or explicit call).
- Produces a new commit on the same branch containing the summary as its turn content, role `"summary"`.
- The summary turn's JSON includes a `summarizes` field listing the SHAs of the original turn range it replaces:
  ```json
  {
    "version": 1,
    "turn": 48,
    "role": "summary",
    "content": "Summarized context of turns 0-47...",
    "summarizes": ["abc123", "def456", "ghi789"]
  }
  ```
- Original turns are not deleted or altered. Context assembly, when budget-constrained, substitutes the summary turn for the original range; full history remains queryable via the referenced SHAs.

### 6.4 Context Assembly Pipeline

Uses an **incremental strategy** to avoid O(N) walks per LLM call:

1. Maintain a context cache keyed on `(branch, turn N)`. On cache hit, only new turns since N are processed and appended.
2. On cache miss (first call or after rewind), walk branch history from root to N and build the full context. Store in cache.
3. Apply active summarization substitutions (6.3) where present — resolved from the `summarizes` field in summary turns, not from commit trailers.
4. Apply deduplication (6.2) to remaining file-read turns using the in-memory index.
5. Optionally augment with ChromaDB retrieval results (5.2) if RAG is enabled for this agent.
6. Serialize to the model's message format.

This pipeline is a pure function of (branch, turn N, config); it does not mutate the log repo. The cache is invalidated when the branch is rewound or when a non-incremental write occurs (e.g., manual ref update).

---

## 7. Tooling: MCP Integration

MCP server lifecycle management is handled by `mcpmon`, which abstracts away process spawning, health checks, restarts, and graceful shutdown.

### 7.1 Bundled Servers

| Server | Package | Purpose |
|---|---|---|
| git | `@modelcontextprotocol/server-git` | Workspace repo introspection/operations |
| filesystem | `@modelcontextprotocol/server-filesystem` | File I/O within workspace |
| fetch | `@modelcontextprotocol/server-fetch` | HTTP fetch |
| search | `@mcp-server/duckduckgo-search` | Web search |
| browser | `@playwright/mcp` | Browser automation |

### 7.2 Registration

```ts
interface MCPServerConfig {
  name: string;
  command: string;
  args: string[];
  env?: Record<string, string>;
}
```

- `mcpmon` manages the full lifecycle: process spawn, MCP handshake, tool discovery (`tools/list`), health monitoring, auto-restart on crash with exponential backoff, and graceful shutdown on session end.
- Adding a new server requires only a new `MCPServerConfig` entry; no other code changes.
- Tool name collisions across servers are namespaced as `<server-name>.<tool-name>` in the agent-facing tool list.
- If a server becomes unavailable mid-session (crash, OOM), `mcpmon` marks its tools as unavailable and attempts restart. The agent is notified of degraded tool access; the session does not crash.

---

## 8. Model Routing (LiteLLM)

- All LLM calls (chat completion, embeddings where supported) go through LiteLLM's unified interface.
- Per-agent model configuration is a plain config object (`{ model: string, provider_params: {...} }`), resolvable per session or per subagent (subagents may use a different/cheaper model than their parent).
- Tool-call schema normalization: framework maintains a thin translation layer between the framework's internal tool schema (derived from MCP `tools/list` responses) and each provider's function-calling format, since LiteLLM does not fully normalize schema differences across providers.

---

## 9. CLI

### 9.1 Core Commands

```
agent run <prompt> [--session <id>] [--model <name>]     # start or continue a session
agent log <session-id> [--branch <path>]                  # human-readable turn history
agent tree <session-id>                                   # render agent/subagent branch structure
agent show <sha>                                           # print full turn JSON at commit
agent rewind <session-id> --to <sha> [--run <prompt>]      # checkout branch at sha, optionally re-run from there as new branch
agent diff <sha1> <sha2>                                   # diff turn content between two commits
agent index rebuild                                         # rebuild JSON index (5.1) and vector index (5.2) from git log
agent mcp add <name> <command> [args...]                    # append MCPServerConfig to config
agent trim <session-id>                                     # delete completed subagent branches for a session
agent trim --all                                            # trim all completed subagent branches across sessions
```

### 9.2 Rewind Semantics

`agent rewind <session-id> --to <sha> --run <prompt>`:

1. Resolve `<sha>` to its branch and turn number N.
2. Create a new branch at `<sha>` (not a reset of the existing branch — original branch and all turns after N remain intact and reachable).
3. Append a new turn on the new branch continuing from `<prompt>`.
4. New branch name: `<original-branch>-rewind-<short-sha-of-new-branch-point>` unless `--branch-name` specified.

Rewinding never mutates or deletes existing refs.

---

## 10. Non-Goals

- No merge of subagent branches into parent branches (see 4.3).
- No built-in document loaders/parsers beyond what MCP servers listed in 7.1 provide.
- No authentication/multi-tenancy model; single-operator local-first assumed for v1.
- No transactional guarantees between log and workspace repos — eventual consistency is acceptable; summaries stored in turn JSON provide a stable reference point for context assembly regardless of workspace commit state.