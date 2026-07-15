from __future__ import annotations

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
    stdin_lock: threading.Lock = field(default_factory=threading.Lock)
    stdout_buffer: list[str] = field(default_factory=list)
    stdout_lock: threading.Lock = field(default_factory=threading.Lock)
    _stdout_thread: Optional[threading.Thread] = None


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
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
            )
            inst.process = proc
            inst.stdout_buffer = []
            inst.restart_count += 1

            inst._stdout_thread = threading.Thread(
                target=self._stdout_reader,
                args=(name,),
                daemon=True,
            )
            inst._stdout_thread.start()

            self._discover_tools(name)

            inst.state = ServerState.RUNNING

            threading.Thread(
                target=self._monitor_server,
                args=(name,),
                daemon=True,
            ).start()

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
            self._send_notification(name, "shutdown")
        except Exception:
            pass

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
            inst.stdout_buffer = []

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

    def _send_request(
        self, name: str, request_id: int, method: str, params: Optional[dict] = None
    ) -> dict:
        inst = self._servers.get(name)
        if not inst or not inst.process or not inst.process.stdin:
            raise RuntimeError(f"Server {name} not running")

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            request["params"] = params

        with inst.stdin_lock:
            inst.process.stdin.write(json.dumps(request) + "\n")
            inst.process.stdin.flush()

        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            response = self._read_response(name)
            if response is None:
                time.sleep(0.01)
                continue
            resp_id = response.get("id")
            if resp_id is None:
                continue
            if resp_id == request_id:
                if "error" in response:
                    err = response["error"]
                    raise RuntimeError(
                        f"MCP error {err.get('code')}: {err.get('message')}"
                    )
                return response.get("result", {})

        raise TimeoutError(
            f"Timeout waiting for response to {method} on {name} (id={request_id})"
        )

    def _send_notification(
        self, name: str, method: str, params: Optional[dict] = None
    ) -> None:
        inst = self._servers.get(name)
        if not inst or not inst.process or not inst.process.stdin:
            return

        notification = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            notification["params"] = params

        with inst.stdin_lock:
            inst.process.stdin.write(json.dumps(notification) + "\n")
            inst.process.stdin.flush()

    def _read_response(self, name: str) -> Optional[dict]:
        inst = self._servers.get(name)
        if not inst:
            return None

        with inst.stdout_lock:
            if inst.stdout_buffer:
                line = inst.stdout_buffer.pop(0)
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from {name}: {line}")
                    return None
        return None

    def _stdout_reader(self, name: str) -> None:
        inst = self._servers.get(name)
        if not inst or not inst.process:
            return

        try:
            while inst.process and inst.process.stdout:
                line = inst.process.stdout.readline()
                if not line:
                    break
                line = line.rstrip("\n\r")
                if not line:
                    continue
                with inst.stdout_lock:
                    inst.stdout_buffer.append(line)
        except (ValueError, OSError):
            pass

    def _discover_tools(self, name: str) -> None:
        inst = self._servers[name]

        init_result = self._send_request(
            name,
            1,
            "initialize",
            {
                "protocolVersion": "0.1.0",
                "capabilities": {},
                "clientInfo": {"name": "gitlord", "version": "0.1.0"},
            },
        )

        list_result = self._send_request(name, 2, "tools/list")
        tools_data = list_result.get("tools", [])
        inst.tools = {}
        for t in tools_data:
            tool = ToolInfo(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            )
            inst.tools[t["name"]] = tool

    def _monitor_server(self, name: str) -> None:
        inst = self._servers[name]
        while self._running and inst.state in (
            ServerState.RUNNING,
            ServerState.DEGRADED,
        ):
            if inst.process is None:
                break
            retcode = inst.process.poll()
            if retcode is not None:
                stderr = ""
                if inst.process.stderr:
                    try:
                        stderr = inst.process.stderr.read()
                    except Exception:
                        pass
                inst.last_error = (
                    f"Exited with code {retcode}: {stderr[:200]}"
                )
                inst.state = ServerState.STOPPED
                logger.warning(
                    f"MCP server {name} exited (code {retcode}), restarting..."
                )
                self.restart_server(name)
                break
            time.sleep(0.5)

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
