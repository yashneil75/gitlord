import json
import pytest
from gitlord.git import GitRepo
from gitlord.schemas import Turn, TurnRole


@pytest.fixture
def repo(tmp_path):
    return GitRepo(tmp_path / "log")


class TestStructuredTrailers:
    def test_new_trailers_in_commit_message(self, repo: GitRepo):
        root = repo.create_orphan_branch("refs/agents/trailer-test")
        turn = Turn(
            turn=1,
            role=TurnRole.assistant,
            content="response",
            agent_id="agent-1",
            turn_id="01ABCDEF",
            tokens=250,
            cost=0.025,
            error=None,
            tool_calls=[{"name": "read", "result": "ok"}],
            subagent_id="01SUBAGENT",
            parent_sha="abc123",
        )
        sha = repo.commit_turn(root, turn, "agent-1", None)

        msg = repo.get_commit_message(sha)
        assert "Turn-ID: 01ABCDEF" in msg
        assert "Turn-Tokens: 250" in msg
        assert "Turn-Cost: 0.025" in msg
        assert "Turn-Error: none" in msg
        assert "Tool-Calls:" in msg
        assert "Subagent-ID: 01SUBAGENT" in msg
        assert "Parent-SHA: abc123" in msg

    def test_parse_new_trailers(self, repo: GitRepo):
        root = repo.create_orphan_branch("refs/agents/trailer-parse")
        turn = Turn(
            turn=1,
            role=TurnRole.tool_call,
            content="",
            agent_id="agent-1",
            turn_id="01XYZ",
            tokens=100,
            cost=0.01,
            error="timeout",
            tool_calls=[{"name": "fetch", "result": "error"}],
        )
        sha = repo.commit_turn(root, turn, "agent-1", None)

        trailers = repo.parse_trailers(sha)
        assert trailers is not None
        assert trailers.turn_id == "01XYZ"
        assert trailers.tokens == 100
        assert trailers.cost == 0.01
        assert trailers.error == "timeout"
        assert trailers.tool_calls == [{"name": "fetch", "result": "error"}]

    def test_backward_compat_old_format(self, repo: GitRepo):
        root = repo.create_orphan_branch("refs/agents/backward")
        turn = Turn(
            turn=0,
            role=TurnRole.system,
            content="init",
            agent_id="agent-1",
        )
        sha = repo.commit_turn(root, turn, "agent-1", None)

        trailers = repo.parse_trailers(sha)
        assert trailers is not None
        assert trailers.turn_id is None
        assert trailers.tokens == 0
        assert trailers.cost == 0.0
        assert trailers.error is None
