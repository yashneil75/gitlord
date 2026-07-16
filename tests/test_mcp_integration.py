import time
import sys
from pathlib import Path

import pytest

from gitlord.mcp import MCPMon, ServerState, ToolInfo
from gitlord.schemas import MCPServerConfig


@pytest.fixture
def real_script() -> Path:
    return Path(__file__).parent / "real_mcp_server.py"


@pytest.fixture
def real_config(real_script: Path) -> MCPServerConfig:
    return MCPServerConfig(
        name="fastmcp-test",
        command=sys.executable,
        args=[str(real_script)],
    )


@pytest.fixture
def real_mon(real_config: MCPServerConfig) -> MCPMon:
    m = MCPMon([real_config])
    yield m
    m.stop_all()


class TestRealMCPIntegration:
    def test_discovers_calculator_and_echo_tools(self, real_mon: MCPMon):
        real_mon.start_server("fastmcp-test")
        time.sleep(1)

        tools = real_mon.get_all_tools(namespace=False)
        tool_names = list(tools.keys())

        assert "calculator" in tool_names
        assert "echo" in tool_names

        calc_info = tools["calculator"]
        assert "operation" in str(calc_info.input_schema)
        assert "a" in str(calc_info.input_schema)
        assert "b" in str(calc_info.input_schema)

        echo_info = tools["echo"]
        assert "msg" in str(echo_info.input_schema)

    def test_call_calculator_add(self, real_mon: MCPMon):
        real_mon.start_server("fastmcp-test")
        time.sleep(1)

        result = real_mon.call_tool("fastmcp-test", "calculator", {
            "operation": "add",
            "a": 3.0,
            "b": 4.0,
        })

        assert result.strip() == "7.0"

    def test_call_calculator_divide_by_zero(self, real_mon: MCPMon):
        real_mon.start_server("fastmcp-test")
        time.sleep(1)

        result = real_mon.call_tool("fastmcp-test", "calculator", {
            "operation": "divide",
            "a": 10.0,
            "b": 0.0,
        })

        assert "division by zero" in result

    def test_call_echo(self, real_mon: MCPMon):
        real_mon.start_server("fastmcp-test")
        time.sleep(1)

        result = real_mon.call_tool("fastmcp-test", "echo", {
            "msg": "hello world",
        })

        assert result.strip() == "You said: hello world"

    def test_namespaced_tools(self, real_mon: MCPMon):
        real_mon.start_server("fastmcp-test")
        time.sleep(1)

        tools = real_mon.get_all_tools(namespace=True)
        assert "fastmcp-test.calculator" in tools
        assert "fastmcp-test.echo" in tools

    def test_call_tool_before_start_raises(self, real_mon: MCPMon):
        with pytest.raises(RuntimeError):
            real_mon.call_tool("fastmcp-test", "calculator", {"operation": "add", "a": 1, "b": 2})

    def test_call_nonexistent_tool_raises(self, real_mon: MCPMon):
        real_mon.start_server("fastmcp-test")
        time.sleep(1)

        with pytest.raises(Exception):
            real_mon.call_tool("fastmcp-test", "nonexistent", {})

    def test_multiple_calls_same_session(self, real_mon: MCPMon):
        real_mon.start_server("fastmcp-test")
        time.sleep(1)

        r1 = real_mon.call_tool("fastmcp-test", "echo", {"msg": "first"})
        r2 = real_mon.call_tool("fastmcp-test", "echo", {"msg": "second"})
        assert "first" in r1
        assert "second" in r2
