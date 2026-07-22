# Architecture

## Overview

GitLord uses a **two-repo model** with git plumbing operations for all log writes. No working tree checkouts are needed for turn commits — everything is done via `hash-object`, `mktree`, `commit-tree`, and `update-ref`.

## Two-Repo Model

| Repository | Purpose | Write Method |
|-----------|---------|--------------|
| **Log repo** | Stores turn history as git commits | Plumbing only (no working tree) |
| **Workspace repo** | Stores agent's actual files | Standard working-tree commits |

The repos are not merged. A turn that mutates workspace files stores the workspace commit SHA in its JSON (`workspace_commit` field) as a cross-reference.

## Ref Namespace

```
refs/agents/<session-id>                              # root session branch
refs/agents/sub/<session-id>/<subagent-id>            # first-level subagent
refs/agents/sub/<session-id>/<subagent-id>/<sub>      # nested subagent
```

- `session-id`: ULID generated at session start
- `subagent-id`: ULID generated at subagent spawn
- Branch depth capped at `max_depth` (default: 8)

## Working Tree Layout (per branch)

```
turns/
  00000000000000000000-system.json
  00000000000000000001-user.json
  00000000000000000002-assistant.json
  00000000000000000003-tool_call.json
  00000000000000000004-tool_result.json
```

One file per turn. Filename: zero-padded 20-digit turn number + role + `.json`.

## How It Works

### Turn Commit Sequence

1. `hash-object` the turn JSON → blob SHA
2. Read parent tree, insert new blob at `turns/NNN...-role.json` → new tree via `mktree`
3. `commit-tree` with parent → commit SHA
4. `update-ref` with compare-and-swap (CAS) → handles concurrent writers

No `git add`, no staging area, no working tree. This enables parallel subagents on different branches without lock contention.

### Session Lifecycle

```
Session.create(id)
  → create_orphan_branch(refs/agents/<id>)
  → commit system turn (turn 0)

Session.append_turn(turn)
  → hash-object → mktree → commit-tree → update-ref (CAS)
  → rebuild index if auto_index=True

Session.rewind(sha)
  → verify commit is on branch
  → create new branch at target SHA (original preserved)
  → return new Session pointing at new branch
```

### Subagent Flow

```
SubagentManager.spawn(parent_branch)
  → create branch at parent tip
  → append system turn on subagent branch
  → return (subagent_id, branch)

SubagentManager.complete(subagent_id, sha)
  → record final SHA
  → enqueue in parent's FIFO queue

SubagentManager.drain_queue(parent_branch)
  → for each queued result:
      → append tool_result turn to parent with Subagent-Result trailer
      → delete subagent branch (unless keep_subagent_branches)
```

### Context Assembly

```
ContextAssembler.assemble(branch, up_to_turn, budget_tokens)
  → check cache for (branch, up_to_turn)
  → walk branch history root → N
  → apply summary substitutions (from `summarizes` field)
  → apply dedup (file reads with same content hash → "see turn N")
  → enforce token budget (newest-first, cumulative)
  → optionally prepend RAG results
  → cache and return messages array
```

### Concurrency Model

- **Log repo**: Plumbing-only commits. Parallel subagents on different branches don't contend. Ref updates use CAS with retry on conflict.
- **Parent append queue**: Subagent completions are enqueued, not applied immediately. Parent processes serially after its current turn ends.
- **Workspace repo**: Parallel subagents get `git worktree add` for isolated file access. Worktrees removed on completion.
