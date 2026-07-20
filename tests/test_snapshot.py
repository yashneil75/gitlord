import json
from pathlib import Path
from gitlord.git import GitRepo
from gitlord.session import Session
from gitlord.schemas import SessionConfig


def _config(tmp_path):
    return SessionConfig(
        log_repo_path=str(tmp_path / "log"),
        workspace_repo_path=str(tmp_path / "ws"),
    )


class TestSnapshot:
    def test_snapshot_compresses_old_turns(self, tmp_path):
        config = _config(tmp_path)
        session = Session.create("snap-test", config)

        for i in range(10):
            session.append_user_turn(f"turn {i}")

        commits_before = session.log_repo.log_branch(
            session.branch, format="%H", reverse=True
        )
        count_before = len(commits_before)

        session.snapshot(keep_recent=3)

        commits_after = session.log_repo.log_branch(
            session.branch, format="%H", reverse=True
        )
        count_after = len(commits_after)
        assert count_after < count_before

    def test_snapshot_creates_snapshot_json(self, tmp_path):
        config = _config(tmp_path)
        session = Session.create("snap-json", config)

        for i in range(5):
            session.append_user_turn(f"msg {i}")

        session.snapshot(keep_recent=2)

        snapshot_path = tmp_path / "ws" / ".gitlord" / "snapshot.json"
        assert snapshot_path.exists()
        with open(snapshot_path) as f:
            snap = json.load(f)
        assert "turns" in snap
        assert len(snap["turns"]) > 0

    def test_snapshot_preserves_recent_turns(self, tmp_path):
        config = _config(tmp_path)
        session = Session.create("snap-preserve", config)

        for i in range(8):
            session.append_user_turn(f"turn {i}")

        session.snapshot(keep_recent=3)

        turns = session.get_turns()
        recent_contents = [t.content for t in turns[-3:]]
        for i in range(3):
            assert f"turn {5 + i}" in str(recent_contents)