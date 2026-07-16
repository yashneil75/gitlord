from pathlib import Path

import pytest

from gitlord.subagent import SubagentManager
from gitlord.session import Session
from gitlord.schemas import SessionConfig, TurnRole
from gitlord.git import GitRepo


@pytest.fixture
def config(tmp_path: Path) -> SessionConfig:
    return SessionConfig(
        log_repo_path=str(tmp_path / "log"),
        workspace_repo_path=str(tmp_path / "ws"),
    )


@pytest.fixture
def session(config: SessionConfig) -> Session:
    return Session.create("test-session", config)


@pytest.fixture
def manager(session: Session) -> SubagentManager:
    return SubagentManager(
        log_repo=session.log_repo,
        workspace_repo=session.workspace_repo,
        config=session.config,
        session_id=session.session_id,
    )


def _subagent_branch(session_branch: str, subagent_id: str) -> str:
    return f"refs/agents/sub/{session_branch.removeprefix('refs/agents/')}/{subagent_id}"


class TestSubagentSpawn:
    def test_spawn_creates_branch_and_tracks_active(
        self, manager: SubagentManager, session: Session
    ):
        subagent_id, branch = manager.spawn(session.branch, session.session_id)

        assert subagent_id
        assert branch == _subagent_branch(session.branch, subagent_id)
        assert session.log_repo.ref_exists(branch)
        assert manager.is_active(subagent_id)

    def test_spawn_writes_system_turn_on_subagent_branch(
        self, manager: SubagentManager, session: Session
    ):
        subagent_id, branch = manager.spawn(session.branch, session.session_id)

        last_commit = session.log_repo.read_ref(branch)
        trailers = session.log_repo.parse_trailers(last_commit)
        assert trailers is not None
        assert trailers.role == "system"

        turn = session.log_repo.get_turn_at_commit(last_commit)
        assert turn is not None
        assert turn.role == TurnRole.system
        assert subagent_id in turn.content

    def test_spawn_enforces_depth_limit(
        self, manager: SubagentManager, session: Session
    ):
        manager.config.agent.max_depth = 0
        with pytest.raises(ValueError, match="Max agent depth"):
            manager.spawn(session.branch, session.session_id)

    def test_spawn_rejects_missing_parent(
        self, manager: SubagentManager
    ):
        with pytest.raises(ValueError, match="has no commits"):
            manager.spawn("refs/agents/nonexistent", "agent-id")

    def test_spawn_rejects_when_queue_full(
        self, manager: SubagentManager, session: Session
    ):
        ids = []
        for _ in range(3):
            sid, _ = manager.spawn(session.branch, session.session_id)
            ids.append(sid)

        for i, sid in enumerate(ids):
            manager.complete(sid, f"{i:040x}")

        with pytest.raises(RuntimeError, match="queue at capacity"):
            manager.spawn(session.branch, session.session_id)

    def test_spawn_after_drain_when_queue_was_full(
        self, manager: SubagentManager, session: Session
    ):
        ids = []
        for _ in range(3):
            sid, _ = manager.spawn(session.branch, session.session_id)
            ids.append(sid)

        for i, sid in enumerate(ids):
            manager.complete(sid, f"{i:040x}")

        manager.drain_queue(session.branch)
        manager.spawn(session.branch, session.session_id)


class TestSubagentComplete:
    def test_complete_removes_from_active_and_enqueues(
        self, manager: SubagentManager, session: Session
    ):
        subagent_id, _ = manager.spawn(session.branch, session.session_id)
        final_sha = "a" * 40

        manager.complete(subagent_id, final_sha)

        assert not manager.is_active(subagent_id)

    def test_complete_unknown_subagent_raises(
        self, manager: SubagentManager
    ):
        with pytest.raises(ValueError, match="not found"):
            manager.complete("no-such-agent", "a" * 40)

    def test_complete_invokes_callback(
        self, manager: SubagentManager, session: Session
    ):
        called: list[tuple[str, str]] = []

        def cb(sid: str, sha: str) -> None:
            called.append((sid, sha))

        manager._on_complete = cb
        subagent_id, _ = manager.spawn(session.branch, session.session_id)
        final_sha = "b" * 40

        manager.complete(subagent_id, final_sha)

        assert len(called) == 1
        assert called[0] == (subagent_id, final_sha)


class TestSubagentDrainQueue:
    def test_drain_writes_subagent_result_trailer(
        self, manager: SubagentManager, session: Session
    ):
        subagent_id, sub_branch = manager.spawn(
            session.branch, session.session_id
        )
        final_sha = "c" * 40
        manager.complete(subagent_id, final_sha)
        manager.drain_queue(session.branch)

        parent_sha = session.log_repo.read_ref(session.branch)
        trailers = session.log_repo.parse_trailers(parent_sha)
        assert trailers is not None
        assert trailers.subagent_result == final_sha

    def test_drain_removes_subagent_branch_by_default(
        self, manager: SubagentManager, session: Session
    ):
        subagent_id, sub_branch = manager.spawn(
            session.branch, session.session_id
        )
        manager.complete(subagent_id, "d" * 40)
        manager.drain_queue(session.branch)

        assert not session.log_repo.ref_exists(sub_branch)

    def test_drain_keeps_subagent_branches_when_configured(
        self, manager: SubagentManager, session: Session
    ):
        manager.config.agent.keep_subagent_branches = True
        subagent_id, sub_branch = manager.spawn(
            session.branch, session.session_id
        )
        manager.complete(subagent_id, "e" * 40)
        manager.drain_queue(session.branch)

        assert session.log_repo.ref_exists(sub_branch)

    def test_drain_multiple_subagents_in_order(
        self, manager: SubagentManager, session: Session
    ):
        ids = []
        for _ in range(3):
            sid, _ = manager.spawn(session.branch, session.session_id)
            ids.append(sid)

        for i, sid in enumerate(ids):
            manager.complete(sid, f"{i:040x}")

        manager.drain_queue(session.branch)

        commits = session.log_repo.log_branch(
            session.branch, format="%H", reverse=True
        )
        drain_commits = commits[-3:]
        for i, c in enumerate(drain_commits):
            trailers = session.log_repo.parse_trailers(c)
            assert trailers is not None
            assert trailers.subagent_result == f"{i:040x}"

    def test_drain_empty_queue_no_effect(
        self, manager: SubagentManager, session: Session
    ):
        before = session.log_repo.read_ref(session.branch)
        manager.drain_queue(session.branch)
        after = session.log_repo.read_ref(session.branch)
        assert after == before


class TestSubagentTrim:
    def test_trim_removes_completed_subagent_branches(
        self, manager: SubagentManager, session: Session
    ):
        subagent_id, sub_branch = manager.spawn(
            session.branch, session.session_id
        )
        manager.complete(subagent_id, "f" * 40)

        count = manager.trim(session_id=session.session_id)

        assert not session.log_repo.ref_exists(sub_branch)
        assert count >= 1

    def test_trim_preserves_active_subagents(
        self, manager: SubagentManager, session: Session
    ):
        subagent_id, sub_branch = manager.spawn(
            session.branch, session.session_id
        )

        count = manager.trim(session_id=session.session_id)

        assert session.log_repo.ref_exists(sub_branch)
        assert count == 0

    def test_trim_preserves_root_session_branch(
        self, manager: SubagentManager, session: Session
    ):
        count = manager.trim(session_id=session.session_id)
        assert session.log_repo.ref_exists(session.branch)
        assert count == 0

    def test_trim_all_sessions(
        self, manager: SubagentManager, session: Session
    ):
        subagent_id, sub_branch = manager.spawn(
            session.branch, session.session_id
        )
        manager.complete(subagent_id, "g" * 40)

        count = manager.trim(all_sessions=True)

        assert not session.log_repo.ref_exists(sub_branch)
        assert count >= 1

    def test_trim_without_keep_active(
        self, manager: SubagentManager, session: Session
    ):
        subagent_id, sub_branch = manager.spawn(
            session.branch, session.session_id
        )

        count = manager.trim(
            session_id=session.session_id, keep_active=False
        )

        assert not session.log_repo.ref_exists(sub_branch)
        assert count >= 1


class TestDepthCalculation:
    def test_root_session_depth(self, manager: SubagentManager):
        assert manager._get_depth("refs/agents/session-123") == 0

    def test_one_level_subagent_depth(self, manager: SubagentManager):
        depth = manager._get_depth(
            "refs/agents/sub/session-123/subagent-456"
        )
        assert depth == 1

    def test_two_level_subagent_depth(self, manager: SubagentManager):
        depth = manager._get_depth(
            "refs/agents/sub/session-123/subagent-456/subagent-789"
        )
        assert depth == 2
