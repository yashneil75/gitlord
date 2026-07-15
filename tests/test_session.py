from pathlib import Path

import pytest

from gitlord.session import Session
from gitlord.schemas import SessionConfig, Turn, TurnRole
from gitlord.git import GitRepo


@pytest.fixture
def config(tmp_path: Path) -> SessionConfig:
    return SessionConfig(
        log_repo_path=str(tmp_path / "log"),
        workspace_repo_path=str(tmp_path / "ws"),
    )


class TestSessionCreate:
    def test_create_sets_up_orphan_branch_and_system_turn(self, config: SessionConfig):
        session = Session.create("test-session", config)

        assert session.session_id == "test-session"
        assert session.branch == "refs/agents/test-session"
        assert session.log_repo.ref_exists(session.branch)

        turns = session.get_turns()
        assert len(turns) == 1
        assert turns[0].turn == 0
        assert turns[0].role == TurnRole.system
        assert turns[0].agent_id == "test-session"
        assert "Session started at" in turns[0].content

    def test_create_duplicate_raises_error(self, config: SessionConfig):
        Session.create("dup-session", config)
        with pytest.raises(ValueError, match="already exists"):
            Session.create("dup-session", config)


class TestSessionResume:
    def test_resume_existing(self, config: SessionConfig):
        Session.create("resume-me", config)
        session = Session.resume("resume-me", config)
        assert session.session_id == "resume-me"
        assert session.log_repo.ref_exists(session.branch)

    def test_resume_nonexistent_raises_error(self, config: SessionConfig):
        with pytest.raises(ValueError, match="not found"):
            Session.resume("no-such-session", config)


class TestSessionAppendTurn:
    def test_append_user_turn(self, config: SessionConfig):
        session = Session.create("user-turn-test", config)
        sha = session.append_user_turn("Hello from user")
        assert sha
        assert session.log_repo.commit_exists(sha)

        turns = session.get_turns()
        assert len(turns) == 2
        assert turns[1].turn == 1
        assert turns[1].role == TurnRole.user
        assert turns[1].content == "Hello from user"

    def test_append_assistant_turn(self, config: SessionConfig):
        session = Session.create("asst-turn-test", config)
        sha = session.append_assistant_turn(
            "Hello from assistant",
            model="gpt-4",
            tokens_in=50,
            tokens_out=100,
        )
        assert sha

        turns = session.get_turns()
        assert len(turns) == 2
        assert turns[1].role == TurnRole.assistant
        assert turns[1].content == "Hello from assistant"
        assert turns[1].model == "gpt-4"
        assert turns[1].tokens_in == 50
        assert turns[1].tokens_out == 100

    def test_append_tool_call_turn(self, config: SessionConfig):
        session = Session.create("tool-call-test", config)
        sha = session.append_tool_call_turn(
            "get_weather",
            {"location": "NYC"},
            tags=["weather"],
        )
        assert sha

        turns = session.get_turns()
        assert len(turns) == 2
        assert turns[1].role == TurnRole.tool_call
        assert turns[1].tool_name == "get_weather"
        assert turns[1].tool_input == {"location": "NYC"}
        assert "weather" in turns[1].tags

    def test_append_tool_result_turn(self, config: SessionConfig):
        session = Session.create("tool-result-test", config)
        sha = session.append_tool_result_turn(
            "get_weather",
            '{"temp": 72}',
        )
        assert sha

        turns = session.get_turns()
        assert len(turns) == 2
        assert turns[1].role == TurnRole.tool_result
        assert turns[1].tool_name == "get_weather"
        assert turns[1].tool_output == '{"temp": 72}'

    def test_append_summary_turn(self, config: SessionConfig):
        session = Session.create("summary-test", config)
        sha = session.append_summary_turn(
            "Summary content",
            summarizes=["commit1"],
            model="gpt-4",
        )
        assert sha

        turns = session.get_turns()
        assert len(turns) == 2
        assert turns[1].role == TurnRole.summary
        assert turns[1].summarizes == ["commit1"]

    def test_multiple_turns_have_sequential_numbers(self, config: SessionConfig):
        session = Session.create("seq-test", config)
        session.append_user_turn("first")
        session.append_assistant_turn("response", model="gpt-4")
        session.append_user_turn("second")

        turns = session.get_turns()
        assert len(turns) == 4
        assert turns[0].turn == 0  # system
        assert turns[1].turn == 1  # user
        assert turns[2].turn == 2  # assistant
        assert turns[3].turn == 3  # user

    def test_append_turn_generic_method(self, config: SessionConfig):
        session = Session.create("generic-turn-test", config)
        turn = Turn(
            turn=0,
            role=TurnRole.user,
            content="generic turn",
            agent_id="custom-agent",
        )
        sha = session.append_turn(turn)
        assert sha

        turns = session.get_turns()
        assert len(turns) == 2
        assert turns[1].content == "generic turn"
        assert turns[1].turn == 1


class TestSessionGetTurns:
    def test_get_turns_with_range(self, config: SessionConfig):
        session = Session.create("range-test", config)
        session.append_user_turn("turn 1")
        session.append_assistant_turn("turn 2", model="gpt-4")
        session.append_user_turn("turn 3")

        all_turns = session.get_turns()
        assert len(all_turns) == 4

        subset = session.get_turns(start=1, end=2)
        assert len(subset) == 2
        assert subset[0].turn == 1
        assert subset[1].turn == 2

        system_only = session.get_turns(start=0, end=0)
        assert len(system_only) == 1
        assert system_only[0].turn == 0

    def test_get_turns_on_new_session(self, config: SessionConfig):
        session = Session.create("new-session", config)
        turns = session.get_turns()
        assert len(turns) == 1
        assert turns[0].role == TurnRole.system


class TestSessionRewind:
    def test_rewind_creates_new_branch(self, config: SessionConfig):
        session = Session.create("rewind-test", config)
        session.append_user_turn("turn 1")
        session.append_assistant_turn("turn 2", model="gpt-4")

        turns = session.get_turns()
        target_turn = turns[1]
        target_sha = session.log_repo.log_branch(session.branch, format="%H", reverse=False)[1]

        rewind_session = session.rewind(target_sha)
        assert rewind_session.branch == "refs/agents/rewind-test-rewind-" + target_sha[:12]
        assert rewind_session.log_repo.ref_exists(rewind_session.branch)

        rewind_turns = rewind_session.get_turns()
        assert len(rewind_turns) == 2
        assert rewind_turns[1].turn == 1

    def test_rewind_with_custom_branch_name(self, config: SessionConfig):
        session = Session.create("rewind-custom", config)
        session.append_user_turn("content")
        target_sha = session.log_repo.log_branch(session.branch, format="%H", reverse=False)[1]

        rewind_session = session.rewind(target_sha, branch_name="refs/agents/my-rewind")
        assert rewind_session.branch == "refs/agents/my-rewind"

    def test_rewind_nonexistent_commit_raises_error(self, config: SessionConfig):
        session = Session.create("rewind-bad", config)
        with pytest.raises(ValueError, match="not found"):
            session.rewind("0000000000000000000000000000000000000000")

    def test_rewind_commit_not_on_branch_raises_error(self, config: SessionConfig):
        session1 = Session.create("rewind-a", config)
        session2 = Session.create("rewind-b", config)
        session1.append_user_turn("a1")
        session2.append_user_turn("b1")
        other_sha = session2.log_repo.read_ref(session2.branch)

        with pytest.raises(ValueError, match="not on branch"):
            session1.rewind(other_sha)


class TestSessionTurnCount:
    def test_turn_count(self, config: SessionConfig):
        session = Session.create("count-test", config)
        assert session.get_turn_count() == 1
        session.append_user_turn("hello")
        assert session.get_turn_count() == 2
        session.append_assistant_turn("world", model="gpt-4")
        assert session.get_turn_count() == 3


class TestSessionResumeAfterAppends:
    def test_resume_and_read_turns(self, config: SessionConfig):
        session = Session.create("resume-read", config)
        session.append_user_turn("persist me")
        session.append_assistant_turn("persisted", model="gpt-4")

        resumed = Session.resume("resume-read", config)
        turns = resumed.get_turns()
        assert len(turns) == 3
        assert turns[1].content == "persist me"
        assert turns[2].content == "persisted"

    def test_resume_and_append_more(self, config: SessionConfig):
        session = Session.create("resume-append", config)
        session.append_user_turn("first message")

        resumed = Session.resume("resume-append", config)
        resumed.append_assistant_turn("second message", model="gpt-4")

        turns = resumed.get_turns()
        assert len(turns) == 3
        assert turns[1].content == "first message"
        assert turns[2].content == "second message"
        assert turns[1].turn == 1
        assert turns[2].turn == 2
