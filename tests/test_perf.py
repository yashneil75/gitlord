from __future__ import annotations

import json
from pathlib import Path

import pytest

from gitlord.session import Session
from gitlord.schemas import SessionConfig
from gitlord.git import GitRepo
from gitlord.snapshot import compress_to_snapshot


@pytest.fixture
def config(tmp_path: Path) -> SessionConfig:
    ws_path = tmp_path / "ws"
    ws_path.mkdir()
    return SessionConfig(
        log_repo_path=str(tmp_path / "log"),
        workspace_repo_path=str(ws_path),
        auto_index=True,
        index_path=str(tmp_path / ".gitlord" / "index.json"),
    )


class TestAutoIndex:
    def test_auto_index_on_create(self, config: SessionConfig):
        session = Session.create("auto-idx", config)
        index_path = Path(config.index_path)
        assert index_path.exists()
        data = json.loads(index_path.read_text())
        assert "sessions" in data
        assert "auto-idx" in data["sessions"]

    def test_auto_index_on_append(self, config: SessionConfig):
        session = Session.create("auto-idx2", config)
        session.append_user_turn("hello")
        index_path = Path(config.index_path)
        data = json.loads(index_path.read_text())
        session_data = data["sessions"]["auto-idx2"]
        assert len(session_data["turns"]) == 2

    def test_auto_index_includes_new_turn_info(self, config: SessionConfig):
        session = Session.create("auto-idx3", config)
        session.append_assistant_turn("response", model="gpt-4", tokens_in=100, tokens_out=50)
        index_path = Path(config.index_path)
        data = json.loads(index_path.read_text())
        turns = data["sessions"]["auto-idx3"]["turns"]
        asst_turns = [t for t in turns if t["role"] == "assistant"]
        assert len(asst_turns) == 1
        assert asst_turns[0]["tokens"] == 150

    def test_auto_index_disabled(self, tmp_path: Path):
        ws_path = tmp_path / "ws"
        ws_path.mkdir()
        cfg = SessionConfig(
            log_repo_path=str(tmp_path / "log"),
            workspace_repo_path=str(ws_path),
            auto_index=False,
        )
        session = Session.create("no-auto", cfg)
        index_path = Path(cfg.index_path)
        if index_path.parent.exists():
            possible = index_path.parent / index_path.name
        else:
            possible = index_path
        assert not possible.exists()

    def test_index_contains_turn_id(self, config: SessionConfig):
        session = Session.create("idx-turn-id", config)
        session.append_user_turn("data")
        index_path = Path(config.index_path)
        data = json.loads(index_path.read_text())
        turns = data["sessions"]["idx-turn-id"]["turns"]
        assert turns[1].get("turn_id", "") != ""

    def test_index_contains_cost(self, config: SessionConfig):
        session = Session.create("idx-cost", config)
        session.append_assistant_turn("costly", model="gpt-4", tokens_in=200, tokens_out=100, cost=0.03)
        index_path = Path(config.index_path)
        data = json.loads(index_path.read_text())
        turns = data["sessions"]["idx-cost"]["turns"]
        asst = [t for t in turns if t["role"] == "assistant"]
        assert len(asst) == 1
        assert asst[0]["cost"] == 0.03


class TestSnapshot:
    def test_snapshot_creates_file(self, config: SessionConfig):
        session = Session.create("snap-test", config)
        session.append_user_turn("turn 1")
        session.append_assistant_turn("response", model="gpt-4")
        session.append_user_turn("turn 2")

        snap_path = str(Path(config.index_path).parent / "snapshot.json")
        count = session.snapshot(up_to_turn=2, output_path=snap_path)
        assert count > 0
        assert Path(snap_path).exists()

    def test_snapshot_contains_turns(self, config: SessionConfig):
        session = Session.create("snap-turns", config)
        session.append_user_turn("first user turn")
        session.append_assistant_turn("assistant reply", model="gpt-4")

        snap_path = str(Path(config.index_path).parent / "snap-turns.json")
        count = session.snapshot(up_to_turn=2, output_path=snap_path)
        assert count == 3

        data = json.loads(Path(snap_path).read_text())
        assert data["total_turns"] == 3
        assert len(data["turns"]) == 3

    def test_snapshot_zero_turns(self, config: SessionConfig):
        session = Session.create("snap-zero", config)
        snap_path = str(Path(config.index_path).parent / "snap-zero.json")
        count = session.snapshot(up_to_turn=0, output_path=snap_path)
        assert count > 0

    def test_session_query_method(self, config: SessionConfig):
        session = Session.create("sess-query", config)
        session.append_user_turn("hello")
        session.append_assistant_turn("world", model="gpt-4", tokens_in=50, tokens_out=100, cost=0.01)

        q = session.query()
        assert q.count() >= 3
        results = q.where("tokens > 100").list()
        assert len(results) >= 1

    def test_session_query_with_cost_filter(self, config: SessionConfig):
        session = Session.create("sess-cost", config)
        session.append_assistant_turn("cheap", model="gpt-4", tokens_in=10, tokens_out=20, cost=0.001)
        session.append_assistant_turn("expensive", model="gpt-4", tokens_in=500, tokens_out=500, cost=0.05)

        q = session.query()
        results = q.where("cost > 0.01").list()
        assert len(results) >= 1
        for r in results:
            if r.get("cost", 0) > 0.01:
                break
        else:
            assert False, "expected at least one result with cost > 0.01"
