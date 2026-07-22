# Getting Started

## Installation

**Python 3.11+** required.

```bash
# Core only (pydantic is the only dependency)
pip install gitlord

# Everything (litellm, chromadb, typer, ulid-py, tiktoken, mcp)
pip install gitlord[all]

# Individual extras
pip install gitlord[mcp]       # MCP server support
pip install gitlord[litellm]   # LLM model routing
pip install gitlord[chromadb]  # ChromaDB vector index
pip install gitlord[cli]       # CLI (typer)
```

## Quickstart

```python
from gitlord import Session, SessionConfig, Turn, TurnRole

# Create a session — generates a ULID and an orphan git branch
config = SessionConfig(log_repo_path="log")
session = Session.create("my-agent", config)

# Append turns
session.append_user_turn("What's the weather in London?")

# The assistant turn
session.append_assistant_turn(
    content="It's currently 15°C and cloudy in London.",
    model="gpt-4o",
    tokens_in=12,
    tokens_out=9,
)

# Read back all turns
for turn in session.get_turns():
    print(f"  [{turn.role}] {turn.content[:80]}")
```

## CLI Usage

```bash
# Start a session
gitlord run "What's the weather in London?"

# View turn history
gitlord log my-session

# View branch structure (shows subagent branches)
gitlord tree my-session

# Inspect a specific turn
gitlord show <sha>

# Rewind to a checkpoint and re-run
gitlord rewind my-session --to <sha> --run "Try a different approach"

# Diff two turns
gitlord diff <sha-a> <sha-b>
```

## Configuration

Create a `gitlord.json` in your project root:

```json
{
  "session": {
    "agent": {
      "model": "gpt-4o",
      "max_depth": 8,
      "rag_enabled": false
    },
    "mcp_servers": [
      {
        "name": "filesystem",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]
      }
    ],
    "log_repo_path": "log",
    "workspace_repo_path": "."
  }
}
```

Or use environment variables:

```bash
export GITLORD_MODEL=gpt-4o
export GITLORD_LOG_REPO=./log
export GITLORD_WORKSPACE_REPO=.
```

## Development

```bash
git clone https://github.com/yashneil75/gitlord.git
cd gitlord
pip install -e ".[all]"
pytest tests/
```
