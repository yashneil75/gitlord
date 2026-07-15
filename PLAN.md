# Implementation Plan: GitLord Framework

## Context
Agent orchestration framework using git-backed storage. SPEC.md defines the full spec. The scaffold has 12 modules with structural code. This plan implements proper business logic.

## Global Constraints
- All git tree operations must handle the `turns/` subdirectory (not flat trees)
- Commit trailer format must match SPEC §3.2 exactly
- Turn filenames: zero-padded 20-digit number + `-` + role + `.json`
- All optional imports (litellm, chromadb, typer, ulid) degrade gracefully
- Use subprocess git, not gitpython or isomorphic-git
- No merge commits for subagent results — use `Subagent-Result` trailer only
- Rewind creates new branch, never mutates existing refs
- Session IDs and subagent IDs are ULIDs (fallback to uuid4 hex if ulid-py unavailable)
- Branch ref format: `refs/agents/<session-id>[/<subagent-id>]...`
- Every module must import cleanly with `from gitlord import *`

## Tasks

### Task 1: Git Plumbing — Tree & Commit Construction

**Files:** `gitlord/git.py`

**Spec refs:** §4.4, §3.2

**Description:** Rewrite commit and tree construction to properly handle the `turns/` subdirectory. Add orphan branch creation for session start. Add CAS retry loop. Add turn content extraction from commits.

**Implementation details:**

1. **`_build_tree_with_turn(parent_sha: str | None, turn_filename: str, blob_sha: str) -> str`**
   - If no parent: create `turns` subtree with one entry → root tree with `turns` entry → return root tree SHA
   - If parent: read parent's tree via `rev-parse <parent>:`, get the `turns` subtree, list its entries via `ls-tree`, remove any existing entry with same filename, add new blob entry, create new `turns` subtree via `mktree`, create new root tree with updated `turns` SHA
   - The `turns` subtree entries must be sorted by filename

2. **`_build_commit_message(turn_number: int, role: str, tags: list[str], agent_id: str, parent_agent_id: str | None, tool: str | None, tokens_in: int, tokens_out: int, workspace_commit: str | None, subagent_result: str | None) -> str`**
   - Header: `[turn:N][role:role][tags:a,b] <summary, <=72 chars>`
   - Empty line
   - Trailers: `Turn:`, `Role:`, `Agent:`, `Parent-Agent:`, `Tool:`, `Tokens-In:`, `Tokens-Out:`, `Workspace-Commit:`, `Subagent-Result:`
   - All trailers present, even if value is "none"

3. **`commit_turn(parent_sha: str | None, turn: Turn, agent_id: str, parent_agent_id: str | None, subagent_result: str | None = None) -> str`**
   - Serialize turn to JSON
   - `hash-object` content → blob SHA
   - Build turn filename: `turns/{turn_number:020d}-{role}.json`
   - `_build_tree_with_turn(parent_sha, turn_filename, blob_sha)` → tree SHA
   - `_build_commit_message(...)` → message
   - `commit-tree tree message -p parent` → commit SHA
   - Return commit SHA

4. **`update_ref_cas(ref: str, new_sha: str, old_sha: str | None) -> str`**
   - `update-ref ref new_sha old_sha`
   - If CAS fails (stderr includes "cannot lock ref" or "unexpected object"), re-read ref, recalculate turn number, retry up to 3 times

5. **`create_orphan_branch(ref: str)`**
   - Create initial tree with empty `turns/` subtree
   - `commit-tree` with no parent ("root commit")
   - `update-ref ref <new-sha>` (no CAS, first write)

6. **`get_turn_at_commit(sha: str) -> Turn | None`**
   - Read commit tree, find `turns/` subtree, read newest turn file (last entry lexicographically), parse JSON → Turn

7. **`get_turn_content_raw(sha: str, turn_number: int) -> str | None`**
   - Read tree, list `turns/` entries, find matching filename, return blob content via `git show <blob-sha>`

8. **Keep all existing utility methods** — fix any that conflict with the above.

---

### Task 2: Session Lifecycle

**Files:** `gitlord/session.py`

**Spec refs:** §4.1, §4.2, §9.2

**Description:** Implement proper session lifecycle: start with orphan branch, append turns with CAS retry, rewind by creating new branch.

**Implementation details:**

1. **`Session.__init__`** — same signature, store `_branch` as `refs/agents/<session-id>`.

2. **`Session.create(session_id, config)`**
   - Create `GitRepo` for log and workspace
   - If branch already exists, raise error
   - Call `git.create_orphan_branch(branch)` to create initial commit
   - Append system turn (turn 0)

3. **`Session.resume(session_id, config)`**
   - Verify branch exists
   - Return Session

4. **`Session._commit_turn(turn: Turn, subagent_result: str | None = None) -> str`**
   - Get parent SHA from `read_ref(branch)`
   - Set turn number from `_next_turn_number()`
   - Call `git.commit_turn(parent_sha, turn, ...)`
   - `update_ref_cas(branch, new_sha, old_sha)` with CAS
   - On CAS retry: re-read parent, recalculate turn number, retry
   - Return commit SHA

5. **`Session._next_turn_number() -> int`**
   - Read branch tip
   - Parse commit trailers for last turn number
   - Return last + 1, or 0 if no commits

6. **`Session.append_turn(turn: Turn) -> str`**
   - Set timestamp, turn number, agent_id
   - Delegate to `_commit_turn`

7. **`Session.rewind(target_sha, branch_name=None) -> Session`**
   - Verify commit exists
   - Verify commit is on this session's branch (traversable from branch tip)
   - New branch name: `{self.branch}-rewind-{target_sha[:12]}` or custom
   - `update_ref(new_branch, target_sha)` (no CAS, ref doesn't exist yet)
   - Return new Session with `_branch` set to new branch

8. **Convenience methods:** `append_user_turn`, `append_assistant_turn`, `append_tool_call_turn`, `append_tool_result_turn`, `append_summary_turn`

9. **`get_turns(start, end)` — walk branch commits, parse each turn JSON, return list of Turn objects**

---

### Task 3: Subagent Lifecycle

**Files:** `gitlord/subagent.py`

**Spec refs:** §4.3

**Description:** Implement subagent spawn, completion with queue, branch cleanup.

**Implementation details:**

1. **ULID generation:** Use `ulid-py` if available, fallback to `uuid.uuid4().hex[:26]`.

2. **`SubagentManager.__init__`**
   - Accept log_repo, session, config
   - Track active subagents: dict[subagent_id, {branch, parent_branch, parent_agent_id, spawned_at, final_sha}]
   - Per-parent queues: dict[parent_branch, deque] — max depth = number of active subagents for that parent (dynamic)

3. **`spawn(parent_branch, parent_agent_id) -> (subagent_id, branch)`**
   - Validate depth < max_depth
   - Generate ULID
   - Create branch at parent tip: `update_ref(branch, parent_sha)` (no CAS)
   - Register in active_subagents
   - Add to parent queue (increase depth)
   - Append system turn on subagent branch
   - Return (subagent_id, branch)

4. **`complete(subagent_id, final_sha)`**
   - Record final_sha
   - Remove from active_subagents
   - Enqueue in parent queue
   - Trigger callback if set

5. **`drain_queue(parent_branch)`**
   - Get queue for parent_branch
   - For each item: append tool_result turn to parent branch with Subagent-Result trailer
   - Delete subagent branch (unless keep_subagent_branches)
   - Commit with parent's current tip as parent

6. **`trim(session_id, all_sessions, keep_active=True)`**
   - List refs under prefix
   - Skip refs at depth <= 3 (just root session)
   - Skip active subagents
   - Delete all others via `delete_ref`
   - Return count

7. **Thread safety:** Use threading.Lock for active_subagents and queue access.

---

### Task 4: Context Assembly Pipeline

**Files:** `gitlord/context.py`

**Spec refs:** §6.1–6.4

**Description:** Working context assembly with dedup, summarization, cache, and token budget.

**Implementation details:**

1. **`DedupIndex`**
   - In-memory dict: `{(branch, path): (turn_number, content_hash)}`
   - `get(branch, path) -> (turn, hash) | None`
   - `set(branch, path, turn, content_hash)`
   - `rebuild_from_log(repo, branch)` — walk branch commits, find file-read tool calls, populate index

2. **`ContextCache`**
   - Dict: `{(branch, turn_n): messages[]}`
   - `get(branch, turn_n) -> messages | None`
   - `set(branch, turn_n, messages)`
   - `invalidate(branch)` — clear all entries for a branch

3. **`ContextAssembler.assemble(branch, up_to_turn, budget_tokens, rag_results)`**
   - Check cache for (branch, up_to_turn) hit
   - Walk branch history from root to up_to_turn
   - For each commit:
     - Read turn JSON
     - If role=summary with summarizes: record SHA→content mapping, skip the SHAs from being included individually
     - For tool_call/tool_result: convert to OpenAI message format
   - Apply dedup:
     - For tool results containing file reads, compute content hash
     - Compare against last read of same path on this branch
     - If same hash, replace with `[see turn N — content unchanged]`
   - Apply budget:
     - Walk messages from newest to oldest
     - Accumulate tokens (len//4)
     - Stop when budget exceeded
   - Prepend RAG context as system message if rag_results provided
   - Cache result (if up_to_turn is specified)
   - Return messages array

4. **`_extract_path(turn)` — extract file path from tool_input dict, look for "path" key**

5. **`compute_summary(branch, start_sha, end_sha, summary_content) -> Turn`**
   - Walk commits between start_sha and end_sha
   - Collect SHA list
   - Return Turn with role=summary, summarizes=sha_list

---

### Task 5: MCP Protocol over stdio

**Files:** `gitlord/mcp.py`

**Spec refs:** §7

**Description:** Proper MCP JSON-RPC handshake with subprocess servers. Tool discovery via `tools/list`. Live tool registry.

**Implementation details:**

1. **MCP JSON-RPC message format:**
   - Request: `{"jsonrpc":"2.0","id":n,"method":"method","params":{...}}`
   - Response: `{"jsonrpc":"2.0","id":n,"result":{...}}`
   - Error: `{"jsonrpc":"2.0","id":n,"error":{"code":n,"message":"..."}}`
   - Notification: `{"jsonrpc":"2.0","method":"method","params":{...}}`
   - Messages are separated by `\n` (one per line)

2. **`ServerInstance` class**
   - `config: MCPServerConfig`
   - `process: subprocess.Popen | None`
   - `state: ServerState`
   - `tools: dict[str, ToolInfo]`
   - `stdin_lock, stdout_buffer` for thread-safe I/O

3. **`MCPMon.start_server(name)`**
   - Spawn subprocess with command+args+env
   - Send `initialize` request: `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"0.1.0","capabilities":{},"clientInfo":{"name":"gitlord","version":"0.1.0"}}}`
   - Read response line from stdout
   - Send `tools/list`: `{"jsonrpc":"2.0","id":2,"method":"tools/list"}`
   - Parse response tools array into ToolInfo objects
   - Start monitor thread

4. **`_send_request(server_name, method, params) -> dict`**
   - Atomic write to stdin
   - Read response line from stdout buffer
   - Parse JSON, return result

5. **`MCPMon.stop_server(name)`**
   - Send `shutdown` notification
   - SIGTERM, wait 5s, SIGKILL if still alive
   - Clean up

6. **Tool namespace:** `f"{server_name}.{tool_name}"` — return merged dict from `get_all_tools()`.

7. **Restart with exponential backoff:** 1s, 2s, 4s, ... 30s max.

---

### Task 6: Index Rebuild & CLI Polish

**Files:** `gitlord/index.py`, `gitlord/cli.py`

**Spec refs:** §5.1, §9.1

**Description:** Correct JSON index format matching SPEC exactly. Complete CLI implementation.

**Implementation details:**

1. **`IndexBuilder.rebuild_json_index()`**
   - Walk all refs under `refs/agents/`
   - For each ref, walk commits and parse trailers
   - Build index dict per SPEC §5.1:
     ```json
     {"sessions": {"<id>": {"branch": "...", "turns": [{"sha", "turn", "role", "tags", "tool"}], "subagents": ["id/sub-id"]}}}
     ```
   - Group turns by session, track subagent paths

2. **`IndexBuilder.rebuild_vector_index()`**
   - Clear vector index
   - Walk all commits, index each turn's content
   - Return total chunk count

3. **`IndexBuilder.to_file(path)` — write index JSON and return dict**

4. **CLI commands (all in `gitlord/cli.py`):**
   - `run <prompt>` — create/resume session, append user turn
   - `log <session-id>` — show turn history with SHA, turn, role, tags
   - `tree <session-id>` — render branch structure with indentation
   - `show <sha>` — print full turn JSON
   - `rewind <session-id> --to <sha> [--run <prompt>]` — create branch, optionally append
   - `diff <sha1> <sha2>` — unified diff of turn content
   - `index rebuild` — rebuild both indexes
   - `mcp add <name> <command> [args...]` — append MCPServerConfig to config file
   - `trim <session-id>` — delete completed subagent branches
   - `trim --all` — trim across all sessions
   - Handle missing deps gracefully: if typer not installed, print error message + install instructions

---

### Task 7: Model & RAG Polish

**Files:** `gitlord/model.py`, `gitlord/rag.py`

**Spec refs:** §8, §5.2

**Description:** Review and solidify model routing and RAG integration.

**Implementation details:**

1. **`ModelRouter`** — already well-implemented, verify:
   - Chat completion with tool support
   - Async chat completion
   - Embeddings
   - Provider-specific schema translation (openai, anthropic, google)
   - LiteLLM error handling
   - Graceful degradation when litellm not installed
   - Token usage tracking

2. **`ToolSchemaTranslator`** — verify:
   - OpenAI format: `{"type":"function","function":{"name","description","parameters"}}`
   - Anthropic format: `{"name","description","input_schema"}`
   - Google/Gemini format: `{"function_declarations":[{"name","description","parameters"}]}`
   - Provider inference from model name

3. **`VectorIndex`** — verify:
   - ChromaDB PersistentClient with configurable persist_directory
   - `index_turn(sha, agent_id, turn, content, tags)` — chunk + add with metadata
   - `index_doc(doc_id, content, source)` — chunk + add with metadata
   - `query(query_text, n_results, filter_by)` — return formatted results
   - `query_by_type(query_text, type)` — convenience
   - `delete_turn(sha)`, `delete_doc(doc_id)`, `count()`, `clear()`
   - `_chunk(text)` — split by size with overlap
   - Graceful degradation when chromadb not installed
