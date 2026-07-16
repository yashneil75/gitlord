import sys
import time
from pathlib import Path

import pytest

from gitlord.mcp import MCPMon, ServerState, ToolInfo
from gitlord.schemas import GitlordError, MCPServerConfig


@pytest.fixture
def mock_script() -> Path:
    return Path(__file__).parent / "mock_mcp_server.py"


@pytest.fixture
def config(mock_script: Path) -> MCPServerConfig:
    return MCPServerConfig(
        name="test-server",
        command=sys.executable,
        args=[str(mock_script)],
    )


@pytest.fixture
def mon(config: MCPServerConfig) -> MCPMon:
    m = MCPMon([config])
    yield m
    m.stop_all()


class TestMCPStartup:
    def test_start_server_handshake(self, mon: MCPMon, config: MCPServerConfig):
        mon.start_server(config.name)
        assert mon.get_server_state(config.name) == ServerState.RUNNING

    def test_server_discovers_tools(self, mon: MCPMon, config: MCPServerConfig):
        mon.start_server(config.name)
        tools = mon.get_all_tools(namespace=False)
        assert "list_files" in tools
        assert "read_file" in tools
        assert tools["list_files"].description == "List files in a directory"
        assert tools["read_file"].input_schema == {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }

    def test_get_all_tools_with_namespace(self, mon: MCPMon, config: MCPServerConfig):
        mon.start_server(config.name)
        tools = mon.get_all_tools(namespace=True)
        assert "test-server.list_files" in tools
        assert "test-server.read_file" in tools

    def test_get_all_tools_empty_before_start(self, mon: MCPMon):
        assert mon.get_all_tools() == {}

    def test_start_idempotent(self, mon: MCPMon, config: MCPServerConfig):
        mon.start_server(config.name)
        mon.start_server(config.name)
        assert mon.get_server_state(config.name) == ServerState.RUNNING


class TestMCPStop:
    def test_stop_server(self, mon: MCPMon, config: MCPServerConfig):
        mon.start_server(config.name)
        assert mon.get_server_state(config.name) == ServerState.RUNNING
        mon.stop_server(config.name)
        assert mon.get_server_state(config.name) == ServerState.STOPPED

    def test_stop_nonexistent_does_not_raise(self, mon: MCPMon):
        mon.stop_server("no-such-server")
        assert True

    def test_stop_idempotent(self, mon: MCPMon, config: MCPServerConfig):
        mon.stop_server(config.name)
        mon.stop_server(config.name)
        assert True

    def test_stop_then_no_tools_returned(self, mon: MCPMon, config: MCPServerConfig):
        mon.start_server(config.name)
        mon.stop_server(config.name)
        assert mon.get_all_tools() == {}


class TestMCPToolNamespace:
    def test_multiple_servers_independent_tools(self, mock_script: Path):
        py = sys.executable
        script = str(mock_script)
        configs = [
            MCPServerConfig(name="server-a", command=py, args=[script]),
            MCPServerConfig(name="server-b", command=py, args=[script]),
        ]
        mon = MCPMon(configs)
        try:
            mon.start_all()
            tools = mon.get_all_tools(namespace=True)
            assert "server-a.list_files" in tools
            assert "server-b.list_files" in tools
            assert tools["server-a.list_files"] is not tools["server-b.list_files"]
            tools_flat = mon.get_all_tools(namespace=False)
            assert "list_files" in tools_flat
        finally:
            mon.stop_all()


class TestMCPCallTool:
    def test_call_tool_echo(self, mon: MCPMon, config: MCPServerConfig):
        mon.start_server(config.name)
        result = mon.call_tool(config.name, "echo", {"message": "hello"})
        assert '"hello"' in result

    def test_call_tool_list_files(self, mon: MCPMon, config: MCPServerConfig):
        mon.start_server(config.name)
        result = mon.call_tool(config.name, "list_files", {"path": "/tmp"})
        assert "file1.txt" in result

    def test_call_tool_read_file(self, mon: MCPMon, config: MCPServerConfig):
        mon.start_server(config.name)
        result = mon.call_tool(config.name, "read_file", {"path": "/test.txt"})
        assert "Contents of /test.txt" in result

    def test_call_tool_not_running_raises(self, mon: MCPMon, config: MCPServerConfig):
        with pytest.raises(GitlordError, match="not running"):
            mon.call_tool(config.name, "echo", {"message": "x"})

    def test_call_tool_unknown_tool(self, mon: MCPMon, config: MCPServerConfig):
        mon.start_server(config.name)
        with pytest.raises(Exception):
            mon.call_tool(config.name, "nonexistent", {})

    def test_call_tool_after_stop_raises(self, mon: MCPMon, config: MCPServerConfig):
        mon.start_server(config.name)
        mon.stop_server(config.name)
        with pytest.raises(GitlordError, match="not running"):
            mon.call_tool(config.name, "echo", {"message": "x"})


class TestMCPCrashAndRestart:
    def test_crash_detection_and_restart(self, mon: MCPMon, config: MCPServerConfig):
        mon.start_server(config.name)
        assert mon.get_server_state(config.name) == ServerState.RUNNING

        with pytest.raises(Exception):
            mon.call_tool(config.name, "crash", {})

        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if mon.get_server_state(config.name) == ServerState.RUNNING:
                break
            time.sleep(0.3)
        else:
            pytest.fail("Server did not restart within timeout")

        tools = mon.get_all_tools(namespace=False)
        assert "list_files" in tools


class TestMCPMarkDegraded:
    def test_mark_degraded(self, mon: MCPMon, config: MCPServerConfig):
        mon.start_server(config.name)
        mon.mark_degraded(config.name)
        assert mon.get_server_state(config.name) == ServerState.DEGRADED

    def test_mark_degraded_nonrunning_does_nothing(
        self, mon: MCPMon, config: MCPServerConfig
    ):
        mon.mark_degraded(config.name)
        assert mon.get_server_state(config.name) == ServerState.STOPPED


class TestMCPAddServer:
    def test_add_server_auto_starts_when_running(self, mon: MCPMon, mock_script: Path):
        mon.start_all()
        config = MCPServerConfig(
            name="added-server",
            command=sys.executable,
            args=[str(mock_script)],
        )
        mon.add_server(config)
        assert "added-server" in mon.list_servers()
        assert mon.get_server_state("added-server") == ServerState.RUNNING

    def test_add_server_registers_when_not_running(
        self, mon: MCPMon, mock_script: Path
    ):
        config = MCPServerConfig(
            name="added-server",
            command=sys.executable,
            args=[str(mock_script)],
        )
        mon.add_server(config)
        assert "added-server" in mon.list_servers()
        assert mon.get_server_state("added-server") == ServerState.STOPPED

    def test_add_server_duplicate_raises(self, mon: MCPMon, config: MCPServerConfig):
        with pytest.raises(ValueError, match="already registered"):
            mon.add_server(config)


class TestMCPListServers:
    def test_list_servers(self, mon: MCPMon, config: MCPServerConfig):
        names = mon.list_servers()
        assert config.name in names

    def test_list_servers_empty(self):
        mon = MCPMon([])
        assert mon.list_servers() == []


class TestMCPStartAllStopAll:
    def test_start_all(self, mock_script: Path):
        py = sys.executable
        script = str(mock_script)
        configs = [
            MCPServerConfig(name="s1", command=py, args=[script]),
            MCPServerConfig(name="s2", command=py, args=[script]),
        ]
        mon = MCPMon(configs)
        try:
            mon.start_all()
            assert mon.get_server_state("s1") == ServerState.RUNNING
            assert mon.get_server_state("s2") == ServerState.RUNNING
        finally:
            mon.stop_all()

    def test_stop_all(self, mock_script: Path):
        py = sys.executable
        script = str(mock_script)
        configs = [
            MCPServerConfig(name="s1", command=py, args=[script]),
            MCPServerConfig(name="s2", command=py, args=[script]),
        ]
        mon = MCPMon(configs)
        mon.start_all()
        mon.stop_all()
        assert mon.get_server_state("s1") == ServerState.STOPPED
        assert mon.get_server_state("s2") == ServerState.STOPPED
