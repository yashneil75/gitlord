# CLI Reference

```bash
gitlord <command> [args] [options]
```

Requires `typer` (`pip install gitlord[cli]`).

## Commands

### `gitlord run`

Start or continue a session.

```bash
gitlord run <prompt> [--session <id>] [--model <name>] [--config <path>]
```

| Option | Description |
|--------|-------------|
| `--session` | Session ID (auto-generated ULID if omitted) |
| `--model` | Model name override |
| `--config` | Path to config file |

---

### `gitlord log`

Show human-readable turn history.

```bash
gitlord log <session-id> [--branch <path>]
```

| Option | Description |
|--------|-------------|
| `--branch` | Branch path (default: root session branch) |

**Output format:** `<sha>  turn:<n>  role:<role> [tags]`

---

### `gitlord tree`

Render agent/subagent branch structure with indentation.

```bash
gitlord tree <session-id>
```

---

### `gitlord show`

Print full turn JSON at a commit.

```bash
gitlord show <sha> [--session <id>] [--config <path>]
```

---

### `gitlord rewind`

Rewind to a checkpoint and optionally re-run.

```bash
gitlord rewind <session-id> --to <sha> [--run <prompt>] [--branch-name <name>]
```

| Option | Description |
|--------|-------------|
| `--to` | Target commit SHA (required) |
| `--run` | Prompt to append after rewind |
| `--branch-name` | Custom name for new branch |

**Behavior:** Creates a new branch at `<sha>`. Original branch untouched.

---

### `gitlord diff`

Diff turn content between two commits.

```bash
gitlord diff <sha1> <sha2> [--session <id>] [--config <path>]
```

---

### `gitlord index`

Rebuild JSON and vector indexes from git log.

```bash
gitlord index [--rebuild] [--config <path>]
```

| Option | Description |
|--------|-------------|
| `--rebuild` | Force rebuild of both indexes |

---

### `gitlord mcp add`

Add an MCP server to config.

```bash
gitlord mcp add <name> <command> [args...] [--config <path>]
```

Writes the server config to `gitlord.json`.

---

### `gitlord trim`

Delete completed subagent branches.

```bash
gitlord trim [--session <id>] [--all] [--config <path>]
```

| Option | Description |
|--------|-------------|
| `--session` | Trim subagents for a specific session |
| `--all` | Trim all completed subagent branches across sessions |
