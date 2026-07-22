# MCP Integration API

```python
from gitlord import MCPMon
```

Manages MCP (Model Context Protocol) server lifecycle: process spawning, tool discovery, health monitoring, and crash recovery.

## Constructor

```python
MCPMon(servers: list[MCPServerConfig], workspace_path: str = ".")
```

## Methods

### `start() -> None`

Start all configured MCP servers. Performs MCP handshake (`initialize`) and tool discovery (`tools/list`).

---

### `stop() -> None`

Gracefully shut down all servers. Sends `shutdown` notification, then SIGTERM, then SIGKILL after 5s timeout.

---

### `start_server(name) -> None`

Start a single server by name.

```python
mon.start_server("filesystem")
```

---

### `stop_server(name) -> None`

Stop a single server by name.

---

### `call_tool(server_name, tool_name, arguments) -> str`

Call a tool on a specific server.

```python
result = mon.call_tool("filesystem", "read_file", {"path": "/tmp/test.txt"})
```

**Returns:** Tool result as string

**Raises:**
- `ValueError` — if server or tool not found

---

### `get_all_tools() -> dict[str, ToolInfo]`

Return a merged dict of all tools across all servers, namespaced as `<server>.<tool>`.

```python
tools = mon.get_all_tools()
# {"filesystem.read_file": ToolInfo(...), "fetch.fetch": ToolInfo(...)}
```

---

### `get_server_tools(server_name) -> dict[str, ToolInfo]`

Return tools for a specific server.

---

### `is_healthy(server_name) -> bool`

Check if a server is running and responsive.

---

### `restart_server(name) -> None`

Restart a server with exponential backoff (1s, 2s, 4s, ... 30s max).

## Server States

| State | Description |
|-------|-------------|
| `starting` | Process spawned, awaiting MCP handshake |
| `running` | Healthy and serving tools |
| `degraded` | Crashed, attempting restart |
| `stopped` | Gracefully shut down |

## Bundled Servers

| Server | Command | Purpose |
|--------|---------|---------|
| `git` | `npx -y @modelcontextprotocol/server-git` | Workspace repo operations |
| `filesystem` | `npx -y @modelcontextprotocol/server-filesystem` | File I/O |
| `fetch` | `npx -y @modelcontextprotocol/server-fetch` | HTTP fetch |
| `search` | `npx -y @mcp-server/duckduckgo-search` | Web search |
| `browser` | `npx -y @playwright/mcp` | Browser automation |

## Tool Namespacing

Tool names are namespaced as `<server-name>.<tool-name>` to avoid collisions across servers.

## Crash Recovery

If a server crashes mid-session:
1. Its tools are marked unavailable
2. An exponential backoff restart is attempted
3. The agent is notified of degraded tool access
4. The session does not crash
