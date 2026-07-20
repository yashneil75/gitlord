"""Regression tests for bugs found while stress-testing gitlord.

Each test class maps to one bug:
  - concurrent appends losing turns (CAS retry starvation)
  - drain_queue losing subagent results when a commit fails
  - unvalidated session ids (invalid refs, 'sub' namespace collision)
  - subagent agent_id clobbered on the spawn system turn
  - chunker infinite loop when chunk_overlap >= chunk_size
  - JSON index attributing subagent branches to a phantom 'sub' session
  - rewind failing on repeated rewinds to the same commit
  - CLI `run` unable to continue an existing session
  - tool_call/tool_result pairing assuming adjacent turns
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from gitlord.context import ContextAssembler
from gitlord.git import GitRepo
from gitlord.index import IndexBuilder
from gitlord.schemas import ChunkingConfig, SessionConfig, Turn, TurnRole
from gitlord.session import Session, validate_session_id
from gitlord.subagent import SubagentManager


@pytest.fixture
def config(tmp_path: Path) -> SessionConfig:
    return SessionConfig(
        log_repo_path=str(tmp_path / "log"),
        workspace_repo_path=str(tmp_path / "ws"),
    )


class TestConcurrentAppends:
    def test_no_turns_lost_under_thread_contention(self, config: SessionConfig):
        Session.create("conc", config)
        n_threads, n_turns = 5, 8
        errors: list[str] = []

        def writer(tid: int) -> None:
            sess = Session.resume("conc", config)
            for i in range(n_turns):
                try:
                    sess.append_user_turn(f"writer {tid} turn {i}")
                except Exception as e:  # pragma: no cover - failure detail
                    errors.append(f"{tid}/{i}: {e}")

        threads = [
            threading.Thread(target=writer, args=(t,)) for t in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        turns = Session.resume("conc", config).get_turns()
        assert len(turns) == 1 + n_threads * n_turns
        numbers = [t.turn for t in turns]
        assert len(numbers) == len(set(numbers)), "duplicate turn numbers"


class TestDrainQueueDurability:
    def test_failed_drain_requeues_results(self, config: SessionConfig, monkeypatch):
        session = Session.create("drain", config)
        mgr = SubagentManager(
            session.log_repo, session.workspace_repo, config, "drain"
        )
        sub_id, branch = mgr.spawn(session.branch, "drain")
        final_sha = session.log_repo.read_ref(branch)
        mgr.complete(sub_id, final_sha)

        def boom(self, turn, subagent_result=None):
            raise RuntimeError("simulated commit failure")

        monkeypatch.setattr(Session, "_commit_turn", boom)
        with pytest.raises(RuntimeError):
            mgr.drain_queue(session.branch)
        monkeypatch.undo()

        # the result must still be in the queue; a retry must record it
        mgr.drain_queue(session.branch)
        recorded = [
            session.log_repo.parse_trailers(sha)
            for sha in session.log_repo.log_branch(session.branch)
        ]
        results = [t.subagent_result for t in recorded if t and t.subagent_result]
        assert results == [final_sha]


class TestSessionIdValidation:
    @pytest.mark.parametrize(
        "bad_id",
        ["", "has space", "a..b", "../../escape", "trail.lock", "end.", "a\\b", "sub", "sub/nested"],
    )
    def test_invalid_ids_rejected(self, bad_id: str, config: SessionConfig):
        with pytest.raises(ValueError):
            Session.create(bad_id, config)

    def test_valid_ids_accepted(self):
        for good in ("my-session", "agent_1", "team/proj", "01HXYZ"):
            validate_session_id(good)

    def test_sub_session_cannot_shadow_subagent_namespace(
        self, config: SessionConfig
    ):
        # a session literally named 'sub' would block every subagent spawn
        # repo-wide (refs/agents/sub vs refs/agents/sub/<session>/<ulid>)
        with pytest.raises(ValueError, match="reserved"):
            Session.create("sub", config)


class TestSubagentIdentity:
    def test_spawn_system_turn_keeps_subagent_agent_id(self, config: SessionConfig):
        session = Session.create("root", config)
        mgr = SubagentManager(
            session.log_repo, session.workspace_repo, config, "root"
        )
        sub_id, branch = mgr.spawn(session.branch, "root")
        tip = session.log_repo.read_ref(branch)
        trailers = session.log_repo.parse_trailers(tip)
        assert trailers is not None
        assert sub_id in trailers.agent_id
        assert trailers.parent_agent_id == "root"


class TestChunkingSafety:
    @pytest.mark.parametrize("overlap", [10, 50])
    def test_chunk_terminates_when_overlap_ge_size(self, overlap: int):
        from gitlord.rag import VectorIndex

        vi = VectorIndex.__new__(VectorIndex)
        vi.chunking = ChunkingConfig(chunk_size=10, chunk_overlap=overlap)
        chunks = vi._chunk("hello world this is long enough")
        assert chunks  # must terminate (previously looped forever)
        assert chunks[0] == "hello worl"


class TestIndexSubagentAttribution:
    def test_subagents_grouped_under_real_session(self, config: SessionConfig):
        config.agent.keep_subagent_branches = True
        session = Session.create("realsession", config)
        session.append_user_turn("hi")
        mgr = SubagentManager(
            session.log_repo, session.workspace_repo, config, "realsession"
        )
        sub_id, _branch = mgr.spawn(session.branch, "realsession")

        idx = IndexBuilder(session.log_repo).rebuild_json_index()
        assert "sub" not in idx["sessions"]
        assert sub_id in idx["sessions"]["realsession"]["subagents"]


class TestRepeatedRewind:
    def test_rewind_same_sha_twice_gets_unique_branches(self, config: SessionConfig):
        session = Session.create("rw", config)
        sha1 = session.append_user_turn("one")
        session.append_user_turn("two")

        r1 = session.rewind(sha1)
        r2 = session.rewind(sha1)
        assert r1.get_branch_path() != r2.get_branch_path()
        assert session.log_repo.ref_exists(r1.get_branch_path())
        assert session.log_repo.ref_exists(r2.get_branch_path())


class TestToolCallPairing:
    def test_interleaved_assistant_turn_keeps_pairing(self, config: SessionConfig):
        session = Session.create("pair", config)
        session.append_user_turn("go")
        session.append_tool_call_turn("read_file", {"path": "a.txt"})
        session.append_assistant_turn("(thinking aloud)", model="m")
        session.append_tool_result_turn("read_file", "CONTENT-A")

        asm = ContextAssembler(session.log_repo, config)
        msgs = asm.assemble(session.branch)
        call_ids = {
            tc["id"] for m in msgs for tc in (m.get("tool_calls") or [])
        }
        result_ids = {
            m["tool_call_id"] for m in msgs if m.get("role") == "tool"
        }
        assert result_ids <= call_ids


class TestCliRunContinues:
    def test_run_twice_same_session(self, tmp_path: Path):
        typer = pytest.importorskip("typer")
        from typer.testing import CliRunner
        from gitlord.cli import app

        cfg_file = tmp_path / "gitlord.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "session": {
                        "log_repo_path": str(tmp_path / "log"),
                        "workspace_repo_path": str(tmp_path / "ws"),
                    }
                }
            )
        )
        runner = CliRunner()
        r1 = runner.invoke(
            app, ["run", "hello", "--session", "mysess", "--config", str(cfg_file)]
        )
        assert r1.exit_code == 0, r1.output
        r2 = runner.invoke(
            app,
            ["run", "hello again", "--session", "mysess", "--config", str(cfg_file)],
        )
        assert r2.exit_code == 0, r2.output

        cfg = SessionConfig(
            log_repo_path=str(tmp_path / "log"),
            workspace_repo_path=str(tmp_path / "ws"),
        )
        turns = Session.resume("mysess", cfg).get_turns()
        user_turns = [t for t in turns if t.role == TurnRole.user]
        assert [t.content for t in user_turns] == ["hello", "hello again"]
