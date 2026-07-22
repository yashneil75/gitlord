# MCP Integration

MCP (Model Context Protocol) servers provide tool access to agents. GitLord manages their full lifecycle.

## Adding MCP Servers

### Via Config File

```json
{
  "session": {
    "mcp_servers": [
      {
        "name": "filesystem",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]
      },
      {
        "name": "fetch",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-fetch"]
      }
    ]
  }
}
```

### Via CLI

```bash
gitlord mcp add filesystem npx -y @modelcontextprotocol/server-filesystem .
gitlord mcp add fetch npx -y @modelcontextprotocol/server-fetch
```

### Via Code

```python
from gitlord import MCPMon, MCPServerConfig

mon = MCPMon([
    MCPServerConfig(name="filesystem", command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "."]),
])
mon.start()
```

## Using Tools

```python
# Call a tool
result = mon.call_tool("filesystem", "read_file", {"path": "/tmp/test.txt"})

# List all available tools
tools = mon.get_all_tools()
for name, info in tools.items():
    print(f"{name}: {info.description}")
```

## Bundled Servers

| Server | Package | Purpose |
|--------|---------|---------|
| `git` | `@modelcontextprotocol/server-git` | Workspace repo operations |
| `filesystem` | `@modelcontextprotocol/server-filesystem` | File I/O |
| `fetch` | `@modelcontextprotocol/server-fetch` | HTTP fetch |
| `search` | `@mcp-server/duckduckgo-search` | Web search |
| `browser` | `@playwright/mcp` | Browser automation |

## Tool Namespacing

Tools are namespaced as `<server>.<tool>` to avoid collisions:

```
filesystem.read_file
filesystem.write_file
fetch.fetch
search.web_search
```

## Crash Recovery

If a server crashes:
1. Its tools are marked unavailable
2. Exponential backoff restart: 1s → 2s → 4s → ... → 30s max
3. Agent is notified of degraded access
4. Session continues without crashing

## Shutdown

```python
mon.stop()  # graceful: SIGTERM → wait 5s → SIGKILL
```
