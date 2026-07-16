from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gitlord.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cwd_isolation(tmp_path: Path) -> Path:
    old_cwd = Path.cwd()
    os.chdir(tmp_path)
    config = {
        "session": {
            "log_repo_path": "log",
            "workspace_repo_path": ".",
            "agent": {"model": "gpt-4o"},
            "mcp_servers": [],
        }
    }
    Path("gitlord.json").write_text(json.dumps(config))
    yield tmp_path
    os.chdir(old_cwd)


class TestCliLog:
    def test_log_after_run(self, runner: CliRunner, cwd_isolation: Path):
        runner.invoke(app, ["run", "hello", "--session", "log-test"])
        result = runner.invoke(app, ["log", "log-test"])
        assert result.exit_code == 0
        assert "system" in result.stdout
        assert "user" in result.stdout

    def test_log_shows_turn_numbers(self, runner: CliRunner, cwd_isolation: Path):
        runner.invoke(app, ["run", "hello", "--session", "turn-log"])
        result = runner.invoke(app, ["log", "turn-log"])
        assert "turn:0" in result.stdout
        assert "turn:1" in result.stdout

    def test_log_nonexistent_session_fails(self, runner: CliRunner, cwd_isolation: Path):
        result = runner.invoke(app, ["log", "no-such"])
        assert result.exit_code != 0


class TestCliTree:
    def test_tree_shows_branch(self, runner: CliRunner, cwd_isolation: Path):
        runner.invoke(app, ["run", "test", "--session", "tree-test"])
        result = runner.invoke(app, ["tree", "tree-test"])
        assert result.exit_code == 0
        assert "tree-test" in result.stdout


class TestCliShow:
    def test_show_prints_turn_json(self, runner: CliRunner, cwd_isolation: Path):
        runner.invoke(app, ["run", "content", "--session", "show-test"])
        log_result = runner.invoke(app, ["log", "show-test"])
        sha = log_result.stdout.strip().split("\n")[0].split()[0]

        result = runner.invoke(app, ["show", sha])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "role" in data
        assert "content" in data

    def test_show_bad_sha_exits_nonzero(self, runner: CliRunner, cwd_isolation: Path):
        result = runner.invoke(app, ["show", "0000000000000000000000000000000000000000"])
        assert result.exit_code != 0


class TestCliRun:
    def test_run_creates_session_and_turn(self, runner: CliRunner, cwd_isolation: Path):
        result = runner.invoke(app, ["run", "test prompt", "--session", "run-test"])
        assert result.exit_code == 0
        assert "Session:" in result.stdout
        assert "Turn committed:" in result.stdout

    def test_run_auto_generates_session_id(self, runner: CliRunner, cwd_isolation: Path):
        result = runner.invoke(app, ["run", "auto id"])
        assert result.exit_code == 0
        assert "Session:" in result.stdout

    def test_run_then_log(self, runner: CliRunner, cwd_isolation: Path):
        runner.invoke(app, ["run", "first", "--session", "run-log"])
        result = runner.invoke(app, ["log", "run-log"])
        assert result.exit_code == 0
        assert "turn:1" in result.stdout


class TestCliRewind:
    def test_rewind_creates_new_branch(self, runner: CliRunner, cwd_isolation: Path):
        runner.invoke(app, ["run", "first", "--session", "rewind-test"])
        log_result = runner.invoke(app, ["log", "rewind-test"])
        lines = log_result.stdout.strip().split("\n")
        target_sha = lines[1].split()[0]

        result = runner.invoke(app, ["rewind", "rewind-test", "--to", target_sha])
        assert result.exit_code == 0
        assert "New branch:" in result.stdout

    def test_rewind_invalid_sha_errors(self, runner: CliRunner, cwd_isolation: Path):
        runner.invoke(app, ["run", "content", "--session", "rewind-bad"])
        result = runner.invoke(app, [
            "rewind", "rewind-bad",
            "--to", "0000000000000000000000000000000000000000",
        ])
        assert result.exit_code != 0


class TestCliDiff:
    def test_diff_same_sha_shows_no_differences(self, runner: CliRunner, cwd_isolation: Path):
        runner.invoke(app, ["run", "content", "--session", "diff-test"])
        log_result = runner.invoke(app, ["log", "diff-test"])
        sha = log_result.stdout.strip().split("\n")[1].split()[0]

        result = runner.invoke(app, ["diff", sha, sha])
        assert result.exit_code == 0
        assert "No differences" in result.stdout

    def test_diff_two_shas(self, runner: CliRunner, cwd_isolation: Path):
        runner.invoke(app, ["run", "first", "--session", "diff-two"])
        log_result = runner.invoke(app, ["log", "diff-two"])
        lines = log_result.stdout.strip().split("\n")
        result = runner.invoke(app, ["diff", lines[0].split()[0], lines[1].split()[0]])
        assert result.exit_code == 0


class TestCliIndex:
    def test_index_rebuild(self, runner: CliRunner, cwd_isolation: Path):
        runner.invoke(app, ["run", "hello", "--session", "index-cli"])
        result = runner.invoke(app, ["index", "--rebuild"])
        assert result.exit_code == 0

    def test_index_rebuild_empty(self, runner: CliRunner, cwd_isolation: Path):
        result = runner.invoke(app, ["index", "--rebuild"])
        assert result.exit_code == 0


class TestCliTrim:
    def test_trim_root_session(self, runner: CliRunner, cwd_isolation: Path):
        runner.invoke(app, ["run", "hello", "--session", "trim-test"])
        result = runner.invoke(app, ["trim", "--session", "trim-test"])
        assert result.exit_code == 0

    def test_trim_all(self, runner: CliRunner, cwd_isolation: Path):
        result = runner.invoke(app, ["trim", "--all"])
        assert result.exit_code == 0


class TestCliMCPAdd:
    def test_mcp_add_writes_config(self, runner: CliRunner, cwd_isolation: Path):
        result = runner.invoke(app, ["mcp-add", "test-server", "python"])
        assert result.exit_code == 0
        assert "Added MCP server" in result.stdout
        data = json.loads(Path("gitlord.json").read_text())
        servers = data["session"]["mcp_servers"]
        assert len(servers) == 1
        assert servers[0]["name"] == "test-server"
        assert servers[0]["command"] == "python"

    def test_mcp_add_with_args(self, runner: CliRunner, cwd_isolation: Path):
        result = runner.invoke(app, ["mcp-add", "my-server", "node", "server.js"])
        assert result.exit_code == 0
        data = json.loads(Path("gitlord.json").read_text())
        server = data["session"]["mcp_servers"][0]
        assert server["name"] == "my-server"
        assert server["command"] == "node"
