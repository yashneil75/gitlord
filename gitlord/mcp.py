from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

try:
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from mcp.client.session import ClientSession
    from mcp.types import TextContent

    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False
    stdio_client = None  # type: ignore
    StdioServerParameters = None  # type: ignore
    ClientSession = None  # type: ignore
    TextContent = None  # type: ignore

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
    tools: dict[str, ToolInfo] = field(default_factory=dict)
    restart_count: int = 0
    last_error: Optional[str] = None
    session: Optional[ClientSession] = None


class MCPMon:
    def __init__(
        self,
        servers: list[MCPServerConfig],
        on_tool_change: Optional[Callable[[], None]] = None,
    ):
        if not _MCP_AVAILABLE:
            raise ImportError(
                "MCPMon requires the 'mcp' package. Install it with: pip install gitlord[mcp]"
            )
        self._servers: dict[str, ServerInstance] = {
            s.name: ServerInstance(config=s) for s in servers
        }
        self._on_tool_change = on_tool_change
        self._lock = threading.Lock()
        self._running = False
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="mcp-event-loop"
        )
        self._loop_thread.start()
        self._stop_events: dict[str, asyncio.Event] = {}
        self._lifecycle_tasks: dict[str, asyncio.Task] = {}

    def start_server(self, name: str) -> None:
        inst = self._servers.get(name)
        if not inst:
            raise ValueError(f"Unknown server: {name}")
        if inst.state == ServerState.RUNNING:
            return

        inst.state = ServerState.STARTING
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._start_server_async(name), self._loop
            )
            future.result(timeout=30)
        except Exception as e:
            inst.state = ServerState.STOPPED
            inst.last_error = str(e)
            logger.error(f"Failed to start MCP server {name}: {e}")
            raise

    async def _start_server_async(self, name: str) -> None:
        stop_event = asyncio.Event()
        self._stop_events[name] = stop_event
        ready: asyncio.Future[bool] = self._loop.create_future()

        task = asyncio.ensure_future(
            self._server_lifecycle(name, ready, stop_event)
        )
        self._lifecycle_tasks[name] = task

        try:
            await ready
        except Exception:
            self._lifecycle_tasks.pop(name, None)
            self._stop_events.pop(name, None)
            raise

    async def _server_lifecycle(
        self,
        name: str,
        ready: asyncio.Future[bool],
        stop_event: asyncio.Event,
    ) -> None:
        inst = self._servers[name]
        try:
            params = StdioServerParameters(
                command=inst.config.command,
                args=inst.config.args,
                env=inst.config.env,
            )

            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    inst.session = session
                    await session.initialize()
                    tools_result = await session.list_tools()
                    inst.tools = {}
                    for t in tools_result.tools:
                        inst.tools[t.name] = ToolInfo(
                            name=t.name,
                            description=t.description or "",
                            input_schema=t.inputSchema,
                        )
                    inst.state = ServerState.RUNNING
                    if not ready.done():
                        ready.set_result(True)

                    await stop_event.wait()

        except asyncio.CancelledError:
            inst.state = ServerState.STOPPED
            if not ready.done():
                ready.set_exception(RuntimeError("Server startup cancelled"))
        except Exception as e:
            inst.state = ServerState.STOPPED
            inst.last_error = str(e)
            if not ready.done():
                ready.set_exception(e)
            else:
                logger.warning(f"MCP server {name} lost, restarting: {e}")
                self.restart_server(name)
        finally:
            inst.session = None
            self._lifecycle_tasks.pop(name, None)
            self._stop_events.pop(name, None)

    def stop_server(self, name: str) -> None:
        inst = self._servers.get(name)
        if not inst or inst.state == ServerState.STOPPED:
            return

        inst.state = ServerState.STOPPING
        try:
            stop_event = self._stop_events.get(name)
            if stop_event is not None:
                self._loop.call_soon_threadsafe(stop_event.set)

            task = self._lifecycle_tasks.get(name)
            if task is not None:
                future = asyncio.run_coroutine_threadsafe(
                    self._await_task(task), self._loop
                )
                future.result(timeout=10)
        except Exception as e:
            logger.warning(f"Error stopping server {name}: {e}")
        finally:
            inst.state = ServerState.STOPPED
            inst.session = None
            inst.tools = {}

    async def _await_task(self, task: asyncio.Task) -> None:
        try:
            await task
        except Exception:
            pass

    def start_all(self) -> None:
        self._running = True
        for name in list(self._servers.keys()):
            self.start_server(name)

    def stop_all(self) -> None:
        self._running = False
        for name in list(self._servers.keys()):
            self.stop_server(name)
        remaining = list(self._lifecycle_tasks.keys())
        if remaining:
            for task in list(self._lifecycle_tasks.values()):
                task.cancel()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=5)

    def restart_server(self, name: str) -> None:
        inst = self._servers.get(name)
        if inst:
            inst.state = ServerState.DEGRADED
        self.stop_server(name)
        backoff = 1.0
        max_backoff = 30.0
        while True:
            try:
                self.start_server(name)
                return
            except Exception as e:
                logger.warning(
                    f"Restart {name} failed, retrying in {backoff:.1f}s: {e}"
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> str:
        inst = self._servers.get(server_name)
        if not inst or not inst.session:
            raise RuntimeError(f"Server {server_name} not running")
        if inst.state != ServerState.RUNNING:
            raise RuntimeError(
                f"Server {server_name} is not running (state: {inst.state.value})"
            )

        future = asyncio.run_coroutine_threadsafe(
            self._call_tool_async(server_name, tool_name, arguments),
            self._loop,
        )
        return future.result(timeout=30)

    async def _call_tool_async(
        self, server_name: str, tool_name: str, arguments: dict
    ) -> str:
        inst = self._servers[server_name]
        if not inst.session:
            raise RuntimeError(f"Server {server_name} not running")
        result = await inst.session.call_tool(tool_name, arguments)
        if result.isError:
            content_text = self._extract_content_text(result.content)
            raise RuntimeError(f"Tool call failed: {content_text}")
        return self._extract_content_text(result.content)

    def _extract_content_text(self, content: list) -> str:
        parts = []
        for c in content:
            if isinstance(c, TextContent):
                parts.append(c.text)
            else:
                parts.append(str(c))
        return "\n".join(parts)

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
