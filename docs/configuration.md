# Configuration

GitLord loads configuration from (in priority order):

1. Explicit path passed to `load_config(path)`
2. `gitlord.json` in the current working directory
3. Environment variables

## Config File Format (`gitlord.json`)

```json
{
  "session": {
    "agent": {
      "model": "gpt-4o",
      "provider_params": {},
      "max_depth": 8,
      "keep_subagent_branches": false,
      "rag_enabled": false,
      "chunking": {
        "chunk_size": 512,
        "chunk_overlap": 50
      }
    },
    "mcp_servers": [
      {
        "name": "filesystem",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
        "env": {}
      }
    ],
    "log_repo_path": "log",
    "workspace_repo_path": ".",
    "auto_index": true,
    "index_path": ".gitlord/index.json"
  }
}
```

## Environment Variables

| Variable | Maps To | Default |
|----------|---------|---------|
| `GITLORD_MODEL` | `session.agent.model` | `gpt-4o` |
| `GITLORD_LOG_REPO` | `session.log_repo_path` | `log` |
| `GITLORD_WORKSPACE_REPO` | `session.workspace_repo_path` | `.` |

## SessionConfig

```python
class SessionConfig(BaseModel):
    session_id: Optional[str] = None
    agent: AgentConfig = Field(default_factory=AgentConfig)
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)
    log_repo_path: str = "log"
    workspace_repo_path: str = "."
    auto_index: bool = True
    index_path: str = ".gitlord/index.json"
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `session_id` | `str \| None` | `None` | Session ID (auto-generated ULID if None) |
| `agent` | `AgentConfig` | defaults | Agent-level configuration |
| `mcp_servers` | `list[MCPServerConfig]` | `[]` | MCP server configurations |
| `log_repo_path` | `str` | `"log"` | Path to the log git repository |
| `workspace_repo_path` | `str` | `"."` | Path to the workspace repository |
| `auto_index` | `bool` | `True` | Rebuild index on every append |
| `index_path` | `str` | `".gitlord/index.json"` | Path for the JSON index file |

## AgentConfig

```python
class AgentConfig(BaseModel):
    model: str = "gpt-4o"
    provider_params: dict[str, Any] = Field(default_factory=dict)
    max_depth: int = 8
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    keep_subagent_branches: bool = False
    rag_enabled: bool = False
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | `str` | `"gpt-4o"` | LiteLLM model identifier |
| `provider_params` | `dict` | `{}` | Provider-specific parameters |
| `max_depth` | `int` | `8` | Max subagent nesting depth |
| `chunking` | `ChunkingConfig` | defaults | Document chunking settings |
| `keep_subagent_branches` | `bool` | `False` | Retain subagent branches after completion |
| `rag_enabled` | `bool` | `False` | Enable ChromaDB vector indexing |

## MCPServerConfig

```python
class MCPServerConfig(BaseModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: Optional[dict[str, str]] = None
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Server name (used as namespace prefix for tools) |
| `command` | `str` | Executable command (e.g., `npx`, `uvx`) |
| `args` | `list[str]` | Command arguments |
| `env` | `dict[str, str] \| None` | Extra environment variables |

## ChunkingConfig

```python
class ChunkingConfig(BaseModel):
    chunk_size: int = 512
    chunk_overlap: int = 50
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `chunk_size` | `int` | `512` | Maximum tokens per chunk |
| `chunk_overlap` | `int` | `50` | Overlap between adjacent chunks |
