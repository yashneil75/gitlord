from __future__ import annotations

import json
from pathlib import Path

import pytest

from gitlord.index import IndexBuilder
from gitlord.session import Session
from gitlord.schemas import SessionConfig
from gitlord.git import GitRepo


@pytest.fixture
def config(tmp_path: Path) -> SessionConfig:
    return SessionConfig(
        log_repo_path=str(tmp_path / "log"),
        workspace_repo_path=str(tmp_path / "ws"),
    )


class TestIndexRebuild:
    def test_rebuild_empty_repo_returns_empty_sessions(self, config: SessionConfig):
        repo = GitRepo(config.log_repo_path)
        builder = IndexBuilder(repo)
        result = builder.rebuild_json_index()

        assert "sessions" in result
        assert "built_at" in result
        assert result["sessions"] == {}

    def test_rebuild_with_single_session(self, config: SessionConfig):
        session = Session.create("test-session", config)
        session.append_user_turn("hello world")

        builder = IndexBuilder(session.log_repo)
        result = builder.rebuild_json_index()

        assert "test-session" in result["sessions"]
        s = result["sessions"]["test-session"]
        assert s["branch"] == "refs/agents/test-session"
        assert len(s["turns"]) == 2
        assert s["turns"][0]["turn"] == 0
        assert s["turns"][0]["role"] == "system"
        assert s["turns"][1]["turn"] == 1
        assert s["turns"][1]["role"] == "user"

    def test_rebuild_output_format(self, config: SessionConfig):
        session = Session.create("format-test", config)
        session.append_user_turn("content")

        builder = IndexBuilder(session.log_repo)
        result = builder.rebuild_json_index()

        s = result["sessions"]["format-test"]
        for turn in s["turns"]:
            assert "sha" in turn
            assert "turn" in turn
            assert "role" in turn
            assert "tags" in turn
            assert isinstance(turn["sha"], str)
            assert isinstance(turn["turn"], int)

    def test_rebuild_tracks_tool_turns(self, config: SessionConfig):
        session = Session.create("tool-test", config)
        session.append_user_turn("use a tool")
        session.append_tool_call_turn("read_file", {"path": "foo.py"}, tags=["file_op"])

        builder = IndexBuilder(session.log_repo)
        result = builder.rebuild_json_index()

        turns = result["sessions"]["tool-test"]["turns"]
        tool_turn = [t for t in turns if t["turn"] == 2]
        assert len(tool_turn) == 1
        assert tool_turn[0]["role"] == "tool_call"
        assert tool_turn[0]["tool"] == "read_file"
        assert "file_op" in tool_turn[0]["tags"]

    def test_to_file_writes_valid_json(self, config: SessionConfig, tmp_path: Path):
        Session.create("to-file-test", config)

        builder = IndexBuilder(GitRepo(config.log_repo_path))
        index_path = tmp_path / "index.json"
        result = builder.to_file(str(index_path))

        assert index_path.exists()
        with open(index_path) as f:
            data = json.load(f)
        assert "sessions" in data
        assert data == result

    def test_rebuild_multiple_sessions(self, config: SessionConfig):
        Session.create("session-a", config)
        Session.create("session-b", config)

        builder = IndexBuilder(GitRepo(config.log_repo_path))
        result = builder.rebuild_json_index()

        assert "session-a" in result["sessions"]
        assert "session-b" in result["sessions"]

    def test_rebuild_returns_stats_dict(self, config: SessionConfig):
        session = Session.create("stats-test", config)
        session.append_user_turn("one")
        session.append_assistant_turn("two", model="gpt-4")

        builder = IndexBuilder(session.log_repo)
        result = builder.rebuild_json_index()

        assert isinstance(result, dict)
        assert "sessions" in result
        assert "built_at" in result


class TestIndexVectorRebuild:
    def test_vector_rebuild_no_vector_index_returns_zero(self, config: SessionConfig):
        session = Session.create("vec-test", config)
        session.append_user_turn("content")

        builder = IndexBuilder(session.log_repo, vector_index=None)
        count = builder.rebuild_vector_index()
        assert count == 0
