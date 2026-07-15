from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from gitlord.schemas import MCPServerConfig

logger = logging.getLogger(__name__)


class ServerState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPING = "stopping"


@dataclass
class ToolInfo:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class ServerInstance:
    config: MCPServerConfig
    state: ServerState = ServerState.STOPPED
    process: Optional[subprocess.Popen] = None
    tools: dict[str, ToolInfo] = field(default_factory=dict)
    restart_count: int = 0
    last_error: Optional[str] = None


class MCPMon:
    def __init__(
        self,
        servers: list[MCPServerConfig],
        on_tool_change: Optional[Callable[[], None]] = None,
    ):
        self._servers: dict[str, ServerInstance] = {
            s.name: ServerInstance(config=s) for s in servers
        }
        self._on_tool_change = on_tool_change
        self._lock = threading.Lock()
        self._running = False

    def start_all(self) -> None:
        self._running = True
        for name in list(self._servers.keys()):
            self.start_server(name)

    def stop_all(self) -> None:
        self._running = False
        for name in list(self._servers.keys()):
            self.stop_server(name)

    def start_server(self, name: str) -> None:
        inst = self._servers.get(name)
        if not inst:
            raise ValueError(f"Unknown server: {name}")
        if inst.state == ServerState.RUNNING:
            return

        inst.state = ServerState.STARTING
        try:
            env = os.environ.copy()
            if inst.config.env:
                env.update(inst.config.env)

            proc = subprocess.Popen(
                [inst.config.command] + inst.config.args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
            )
            inst.process = proc
            inst.state = ServerState.RUNNING
            inst.restart_count += 1

            threading.Thread(
                target=self._monitor_server,
                args=(name,),
                daemon=True,
            ).start()

            self._discover_tools(name)

        except Exception as e:
            inst.state = ServerState.STOPPED
            inst.last_error = str(e)
            logger.error(f"Failed to start MCP server {name}: {e}")
            raise

    def stop_server(self, name: str) -> None:
        inst = self._servers.get(name)
        if not inst or not inst.process:
            return

        inst.state = ServerState.STOPPING
        try:
            if os.name == "nt":
                inst.process.terminate()
            else:
                os.kill(inst.process.pid, signal.SIGTERM)
            inst.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            inst.process.kill()
            inst.process.wait(timeout=2)
        except Exception:
            pass
        finally:
            inst.state = ServerState.STOPPED
            inst.process = None

    def restart_server(self, name: str) -> None:
        self.stop_server(name)
        time.sleep(0.5)
        self.start_server(name)

    def _monitor_server(self, name: str) -> None:
        inst = self._servers[name]
        backoff = 1.0
        while self._running and inst.state == ServerState.RUNNING:
            if inst.process is None:
                break
            retcode = inst.process.poll()
            if retcode is not None:
                stderr = ""
                if inst.process.stderr:
                    stderr = inst.process.stderr.read()
                inst.last_error = f"Exited with code {retcode}: {stderr[:200]}"
                inst.state = ServerState.STOPPED
                logger.warning(
                    f"MCP server {name} exited (code {retcode}), "
                    f"restarting in {backoff:.1f}s..."
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                try:
                    self.start_server(name)
                    backoff = 1.0
                except Exception as e:
                    logger.error(f"Failed to restart {name}: {e}")

    def _discover_tools(self, name: str) -> None:
        inst = self._servers[name]
        try:
            stderr_output = ""
            if inst.process and inst.process.stderr:
                stderr_output = inst.process.stderr.read()
            inst.tools = {
                "list": ToolInfo(name="list", description="List available tools"),
            }
        except Exception as e:
            logger.warning(f"Tool discovery for {name} failed: {e}")

    def get_all_tools(self, namespace: bool = True) -> dict[str, ToolInfo]:
        tools: dict[str, ToolInfo] = {}
        for server_name, inst in self._servers.items():
            if inst.state != ServerState.RUNNING:
                continue
            for tool_name, tool_info in inst.tools.items():
                key = f"{server_name}.{tool_name}" if namespace else tool_name
                tools[key] = tool_info
        return tools

    def mark_degraded(self, name: str) -> None:
        inst = self._servers.get(name)
        if inst and inst.state == ServerState.RUNNING:
            inst.state = ServerState.DEGRADED
            if self._on_tool_change:
                self._on_tool_change()

    def get_server_state(self, name: str) -> Optional[ServerState]:
        inst = self._servers.get(name)
        return inst.state if inst else None

    def get_server_info(self, name: str) -> Optional[ServerInstance]:
        return self._servers.get(name)

    def list_servers(self) -> list[str]:
        return list(self._servers.keys())

    def add_server(self, config: MCPServerConfig) -> None:
        if config.name in self._servers:
            raise ValueError(f"Server {config.name} already registered")
        self._servers[config.name] = ServerInstance(config=config)
        if self._running:
            self.start_server(config.name)
