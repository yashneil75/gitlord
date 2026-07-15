import json
from pathlib import Path

import pytest

from gitlord.git import GitRepo, CASError, TURN_FILENAME_RE
from gitlord.schemas import Turn, TurnRole, CommitTrailers


@pytest.fixture
def bare_repo(tmp_path: Path) -> GitRepo:
    return GitRepo(tmp_path / "log")


class TestGitPlumbing:
    def test_create_orphan_branch(self, bare_repo: GitRepo):
        sha = bare_repo.create_orphan_branch("refs/agents/test")
        assert sha
        assert bare_repo.ref_exists("refs/agents/test")
        tree_sha = bare_repo.get_tree(sha)
        entries = bare_repo.ls_tree(tree_sha)
        assert any(name == "turns" for _, _, _, name in entries)

    def test_commit_and_read_turn(self, bare_repo: GitRepo):
        root_sha = bare_repo.create_orphan_branch("refs/agents/test")

        turn = Turn(
            turn=0,
            role=TurnRole.system,
            content="Hello, world!",
            agent_id="test-agent",
        )

        commit_sha = bare_repo.commit_turn(
            parent_sha=root_sha,
            turn=turn,
            agent_id="test-agent",
            parent_agent_id=None,
        )
        assert commit_sha
        assert bare_repo.commit_exists(commit_sha)

        read_turn = bare_repo.get_turn_at_commit(commit_sha)
        assert read_turn is not None
        assert read_turn.turn == 0
        assert read_turn.role == TurnRole.system
        assert read_turn.content == "Hello, world!"
        assert read_turn.agent_id == "test-agent"

    def test_commit_message_format(self, bare_repo: GitRepo):
        root_sha = bare_repo.create_orphan_branch("refs/agents/test-msg")

        turn = Turn(
            turn=1,
            role=TurnRole.assistant,
            content="Test message format",
            agent_id="agent-1",
            tags=["important", "test"],
            tokens_in=100,
            tokens_out=50,
            model="gpt-4",
        )

        commit_sha = bare_repo.commit_turn(
            parent_sha=root_sha,
            turn=turn,
            agent_id="agent-1",
            parent_agent_id=None,
        )

        msg = bare_repo.get_commit_message(commit_sha)
        assert "[turn:1]" in msg
        assert "[role:assistant]" in msg
        assert "[tags:important,test]" in msg
        assert "Turn: 1" in msg
        assert "Role: assistant" in msg
        assert "Agent: agent-1" in msg
        assert "Parent-Agent: none" in msg
        assert "Tool: none" in msg
        assert "Tokens-In: 100" in msg
        assert "Tokens-Out: 50" in msg
        assert "Workspace-Commit: none" in msg
        assert "Subagent-Result: none" in msg

        trailers = bare_repo.parse_trailers(commit_sha)
        assert trailers is not None
        assert trailers.turn == 1
        assert trailers.role == "assistant"
        assert trailers.agent_id == "agent-1"
        assert trailers.parent_agent_id is None
        assert trailers.tool is None
        assert trailers.tokens_in == 100
        assert trailers.tokens_out == 50
        assert trailers.workspace_commit is None
        assert trailers.subagent_result is None

    def test_multiple_turns(self, bare_repo: GitRepo):
        root_sha = bare_repo.create_orphan_branch("refs/agents/multi")
        prev_sha = root_sha

        for i in range(3):
            turn = Turn(
                turn=i,
                role=TurnRole.user if i % 2 == 0 else TurnRole.assistant,
                content=f"Turn {i} content",
                agent_id="test-agent",
            )
            commit_sha = bare_repo.commit_turn(
                parent_sha=prev_sha,
                turn=turn,
                agent_id="test-agent",
                parent_agent_id=None,
            )
            prev_sha = commit_sha

        last_turn = bare_repo.get_turn_at_commit(prev_sha)
        assert last_turn is not None
        assert last_turn.turn == 2
        assert last_turn.content == "Turn 2 content"

        content = bare_repo.get_turn_content_raw(prev_sha, 0)
        assert content is not None
        data = json.loads(content)
        assert data["content"] == "Turn 0 content"

    def test_update_ref_cas_success(self, bare_repo: GitRepo):
        root_sha = bare_repo.create_orphan_branch("refs/agents/cas-ok")

        turn = Turn(
            turn=0,
            role=TurnRole.system,
            content="CAS test",
            agent_id="test-agent",
        )
        commit_sha = bare_repo.commit_turn(
            parent_sha=root_sha,
            turn=turn,
            agent_id="test-agent",
            parent_agent_id=None,
        )

        result = bare_repo.update_ref_cas("refs/agents/cas-ok", commit_sha, root_sha)
        assert result == commit_sha

    def test_update_ref_cas_retry_with_rebuild(self, bare_repo: GitRepo):
        root_sha = bare_repo.create_orphan_branch("refs/agents/cas-rebuild")

        turn0 = Turn(
            turn=0, role=TurnRole.system, content="first", agent_id="agent"
        )
        commit0 = bare_repo.commit_turn(root_sha, turn0, "agent", None)

        bare_repo.update_ref("refs/agents/cas-rebuild", commit0)

        turn1 = Turn(
            turn=1, role=TurnRole.user, content="second", agent_id="agent"
        )
        stale_commit = bare_repo.commit_turn(root_sha, turn1, "agent", None)

        def rebuild(new_parent: str) -> str:
            return bare_repo.commit_turn(new_parent, turn1, "agent", None)

        result = bare_repo.update_ref_cas(
            "refs/agents/cas-rebuild", stale_commit, root_sha, rebuild_fn=rebuild
        )
        assert result != stale_commit
        assert bare_repo.commit_exists(result)
        assert bare_repo.read_ref("refs/agents/cas-rebuild") == result

    def test_update_ref_cas_failure(self, bare_repo: GitRepo):
        bare_repo.create_orphan_branch("refs/agents/cas-fail")
        current = bare_repo.read_ref("refs/agents/cas-fail")

        with pytest.raises(CASError):
            bare_repo.update_ref_cas(
                "refs/agents/cas-fail",
                "0000000000000000000000000000000000000001",
                current,
            )

    def test_get_turn_content_raw_not_found(self, bare_repo: GitRepo):
        root_sha = bare_repo.create_orphan_branch("refs/agents/no-turn")
        result = bare_repo.get_turn_content_raw(root_sha, 999)
        assert result is None

    def test_turn_filename_regex(self):
        m = TURN_FILENAME_RE.match("00000000000000000000-system.json")
        assert m is not None
        assert m.group(1) == "00000000000000000000"
        assert m.group(2) == "system"

        m = TURN_FILENAME_RE.match("00000000000000000001-assistant.json")
        assert m is not None

        m = TURN_FILENAME_RE.match("turns/00000000000000000001-assistant.json")
        assert m is None

    def test_commit_tree_from_turns_backward_compat(self, bare_repo: GitRepo):
        root_sha = bare_repo.create_orphan_branch("refs/agents/backward")

        turn_data = Turn(
            turn=0,
            role=TurnRole.user,
            content="backward compat test",
            agent_id="test-agent",
        )
        content = turn_data.model_dump_json()
        blob_sha = bare_repo.hash_object(content)

        trailers = CommitTrailers(
            turn=0,
            role="user",
            agent_id="test-agent",
        )

        commit_sha = bare_repo.commit_tree_from_turns(
            parent_sha=root_sha,
            turn_number=0,
            role="user",
            blob_sha=blob_sha,
            trailers=trailers,
            tags=[],
        )
        assert commit_sha
        assert bare_repo.commit_exists(commit_sha)

        turn = bare_repo.get_turn_at_commit(commit_sha)
        assert turn is not None
        assert turn.role == TurnRole.user
        assert turn.content == "backward compat test"

    def test_get_turn_filename(self, bare_repo: GitRepo):
        root_sha = bare_repo.create_orphan_branch("refs/agents/tf-test")

        turn = Turn(
            turn=0,
            role=TurnRole.user,
            content="filename test",
            agent_id="test-agent",
        )
        commit_sha = bare_repo.commit_turn(
            parent_sha=root_sha,
            turn=turn,
            agent_id="test-agent",
            parent_agent_id=None,
        )

        tf = bare_repo.get_turn_filename(commit_sha)
        assert tf is not None
        assert tf.startswith("turns/")
        assert tf.endswith(".json")

        content = bare_repo.get_turn_content(commit_sha, tf)
        data = json.loads(content)
        assert data["content"] == "filename test"

    def test_get_turn_at_commit_no_turns(self, bare_repo: GitRepo):
        result = bare_repo.get_turn_at_commit("HEAD")
        assert result is None
