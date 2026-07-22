# Git Repo API

```python
from gitlord import GitRepo
```

Low-level git plumbing operations. All operations use subprocess git (no gitpython, no isomorphic-git).

## Constructor

```python
GitRepo(path: str | Path, bare: bool = False)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | `str \| Path` | Path to the git repository |
| `bare` | `bool` | Whether to create a bare repository |

## Branch Operations

### `create_orphan_branch(ref) -> str`

Create an orphan branch with an initial empty commit.

```python
sha = repo.create_orphan_branch("refs/agents/my-session")
```

**Returns:** Initial commit SHA

---

### `read_ref(ref) -> str | None`

Read the commit SHA a ref points to.

```python
sha = repo.read_ref("refs/agents/my-session")
```

**Returns:** Commit SHA or `None` if ref doesn't exist

---

### `update_ref(ref, new_sha, old_sha=None) -> None`

Update a ref to point to a new commit.

```python
repo.update_ref("refs/agents/my-session", new_commit_sha)
```

---

### `update_ref_cas(ref, new_sha, old_sha, rebuild_fn=None) -> str`

Compare-and-swap ref update. Retries on conflict (concurrent writers).

```python
result_sha = repo.update_ref_cas(
    ref="refs/agents/my-session",
    new_sha=new_commit_sha,
    old_sha=parent_sha,
    rebuild_fn=rebuild_function,  # called on CAS failure to rebuild commit
)
```

**Returns:** The SHA that was successfully written

---

### `ref_exists(ref) -> bool`

Check if a ref exists.

---

### `list_refs(prefix) -> Iterator[str]`

List all refs matching a prefix.

```python
refs = list(repo.list_refs("refs/agents/"))
```

---

### `delete_ref(ref, force=False) -> None`

Delete a git ref.

## Commit Operations

### `commit_turn(parent_sha, turn, agent_id, parent_agent_id, subagent_result=None) -> str`

Build a turn commit using plumbing.

```python
sha = repo.commit_turn(
    parent_sha=parent_sha,
    turn=turn,
    agent_id="session-id",
    parent_agent_id=None,
)
```

**Returns:** Commit SHA

---

### `get_turn_at_commit(sha) -> Turn | None`

Read the turn JSON from a commit.

---

### `get_turn_filename(sha) -> str | None`

Get the turn filename at a commit.

---

### `get_turn_content(sha, turn_filename) -> str`

Read the raw JSON content of a turn file.

---

### `get_turn_content_raw(sha, turn_number) -> str | None`

Read turn content by turn number.

## Tree Operations

### `_build_tree_with_turn(parent_sha, turn_filename, blob_sha) -> str`

Construct a new tree with a turn blob added to the `turns/` subtree.

---

### `_build_commit_message(turn_number, role, tags, agent_id, ...) -> str`

Build a SPEC §3.2 compliant commit message with header and trailers.

## Utility

### `log_branch(ref, format="%H", reverse=True) -> list[str]`

Walk branch history and return commit SHAs.

---

### `parse_trailers(sha) -> CommitTrailers | None`

Parse git commit trailers into a `CommitTrailers` object.

---

### `commit_exists(sha) -> bool`

Check if a commit object exists.

---

### `rev_parse(ref) -> str`

Resolve a ref/abbreviated SHA to a full SHA.

---

### `get_head() -> str | None`

Get the HEAD commit SHA of the workspace repo.

---

### `checkout(sha) -> None`

Check out a specific commit in the workspace repo.
