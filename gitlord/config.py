from __future__ import annotations

import json
import os
from pathlib import Path

from gitlord.schemas import AgentConfig, MCPServerConfig, SessionConfig


class Config:
    def __init__(self, session: SessionConfig | None = None) -> None:
        self.session = session or SessionConfig()

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        session_data = data.get("session", {})
        agent_data = session_data.get("agent", {})
        agent = AgentConfig(**agent_data)
        mcp_servers = [MCPServerConfig(**s) for s in session_data.get("mcp_servers", [])]
        session = SessionConfig(
            agent=agent,
            mcp_servers=mcp_servers,
            log_repo_path=session_data.get("log_repo_path", "log"),
            workspace_repo_path=session_data.get("workspace_repo_path", "."),
        )
        return cls(session=session)

    @classmethod
    def from_file(cls, path: str | Path) -> Config:
        path = Path(path)
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        return cls.from_dict(data)

    @classmethod
    def from_env(cls) -> Config:
        session = SessionConfig()
        if model := os.environ.get("GITLORD_MODEL"):
            session.agent.model = model
        if log_repo := os.environ.get("GITLORD_LOG_REPO"):
            session.log_repo_path = log_repo
        if workspace_repo := os.environ.get("GITLORD_WORKSPACE_REPO"):
            session.workspace_repo_path = workspace_repo
        return cls(session=session)


def load_config(path: str | Path | None = None) -> Config:
    if path:
        return Config.from_file(path)
    config_path = Path("gitlord.json")
    if config_path.exists():
        return Config.from_file(config_path)
    return Config.from_env()
