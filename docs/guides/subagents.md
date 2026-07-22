# Subagent Orchestration

GitLord supports hierarchical subagent orchestration where subagents run on isolated git branches and their results are integrated into the parent via commit trailers.

## Spawning a Subagent

```python
from gitlord import SubagentManager

manager = SubagentManager(
    log_repo=session.log_repo,
    workspace_repo=session.workspace_repo,
    config=session.config,
    session_id=session.session_id,
)

# Spawn — creates a branch at the parent's current tip
subagent_id, branch = manager.spawn(
    parent_branch=session.branch,
    parent_agent_id=session.session_id,
)
```

The subagent gets its own branch under `refs/agents/sub/<session-id>/<subagent-id>` and starts with a system turn.

## Running the Subagent

The subagent appends turns on its own branch:

```python
sub_session = Session.resume(subagent_id, config)
sub_session._branch = branch
sub_session.append_user_turn("Analyze the codebase...")
sub_session.append_assistant_turn("Here's my analysis...", model="gpt-4o")
```

## Completing a Subagent

When the subagent finishes, mark it complete:

```python
final_sha = sub_session.get_turns()[-1]  # or the commit SHA
manager.complete(subagent_id, final_sha)
```

This enqueues the result for parent integration.

## Integrating Results

The parent drains its queue after its current turn:

```python
count = manager.drain_queue(session.branch)
# Appends one turn per subagent with Subagent-Result trailer
```

Each integrated result includes a `Subagent-Result` trailer pointing to the subagent's final commit SHA. No merge commit is created.

## Branch Cleanup

Completed subagent branches are deleted by default. Override with:

```python
session.config.agent.keep_subagent_branches = True
```

Trim manually:

```python
manager.trim(session_id="my-session")
manager.trim(all_sessions=True)
```

## Depth Limits

Subagent nesting is capped at `max_depth` (default: 8). Spawning beyond the cap raises `ValueError`.

```
refs/agents/<session>                              # depth 0
refs/agents/sub/<session>/<sub>                   # depth 1
refs/agents/sub/<session>/<sub>/<sub>             # depth 2
...
```

## Thread Safety

- `SubagentManager` uses `threading.Lock` for active subagent tracking
- Completions are enqueued, not applied immediately
- Parent processes completions serially after its current turn ends
- No lock contention between parallel subagents on different branches
