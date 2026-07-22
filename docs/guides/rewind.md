# Rewind & Fork

Rewind jumps back to any point in your agent's history. The original branch is never mutated.

## Basic Rewind

```python
new_session = session.rewind("abc123")
```

This creates a new branch at commit `abc123` and returns a new `Session` pointing to it. All turns after `abc123` on the original branch remain intact and reachable.

## Rewind with Re-run

```python
new_session = session.rewind("abc123", branch_name="refs/agents/experiment-v2")
new_session.append_user_turn("Try a different approach to the same problem")
```

## Custom Branch Names

```python
new_session = session.rewind(
    "abc123",
    branch_name="refs/agents/my-session-v2",
)
```

Auto-generated names follow the pattern: `<original>-rewind-<short-sha>`

## Via CLI

```bash
# Rewind and re-run
gitlord rewind my-session --to abc123 --run "Try again with more context"

# Rewind with custom branch name
gitlord rewind my-session --to abc123 --branch-name refs/agents/experiment
```

## Workspace State

If the target turn has a `workspace_commit`, the workspace repo is checked out to that state:

```python
# After rewind, workspace matches the state at the target turn
new_session = session.rewind("abc123")
# workspace_repo is now at the state of turn abc123
```

## Viewing History

Both branches remain fully accessible:

```bash
# Original branch — all turns intact
gitlord log my-session

# Rewound branch — continues from the rewind point
gitlord log my-session --branch refs/agents/my-session-rewind-abc123def012
```

## Safety

- Rewind **never deletes or mutates** existing refs
- Original branch and all its turns remain reachable
- Rewind creates a new branch — it's a fork, not a reset
- Concurrent operations on different branches don't interfere
