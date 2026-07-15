from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class TurnRole(str, Enum):
    system = "system"
    user = "user"
    assistant = "assistant"
    tool_call = "tool_call"
    tool_result = "tool_result"
    summary = "summary"


class Turn(BaseModel):
    version: int = Field(default=1, ge=1)
    turn: int = Field(ge=0)
    role: TurnRole
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent_id: str
    parent_agent_id: Optional[str] = None
    content: str = ""
    tool_name: Optional[str] = None
    tool_input: Optional[dict[str, Any]] = None
    tool_output: Optional[Any] = None
    model: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    workspace_commit: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    summarizes: Optional[list[str]] = None


class CommitTrailers(BaseModel):
    turn: int
    role: str
    agent_id: str
    parent_agent_id: Optional[str] = None
    tool: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    workspace_commit: Optional[str] = None
    subagent_result: Optional[str] = None
    tags: list[str] = Field(default_factory=list)


class MCPServerConfig(BaseModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: Optional[dict[str, str]] = None


class ChunkingConfig(BaseModel):
    chunk_size: int = 512
    chunk_overlap: int = 50


class AgentConfig(BaseModel):
    model: str = "gpt-4o"
    provider_params: dict[str, Any] = Field(default_factory=dict)
    max_depth: int = 8
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    keep_subagent_branches: bool = False
    rag_enabled: bool = False


class SessionConfig(BaseModel):
    session_id: Optional[str] = None
    agent: AgentConfig = Field(default_factory=AgentConfig)
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)
    log_repo_path: str = "log"
    workspace_repo_path: str = "."
