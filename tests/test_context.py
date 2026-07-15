import json
from pathlib import Path

import pytest

from gitlord.context import (
    ContextAssembler,
    ContextCache,
    ContextCacheEntry,
    DedupIndex,
)
from gitlord.session import Session
from gitlord.schemas import SessionConfig, TurnRole


@pytest.fixture
def config(tmp_path: Path) -> SessionConfig:
    return SessionConfig(
        log_repo_path=str(tmp_path / "log"),
        workspace_repo_path=str(tmp_path / "ws"),
    )


class TestDedupIndex:
    def test_set_and_get(self):
        index = DedupIndex()
        index.set("branch1", "path/to/file.py", 3, "abc123")
        result = index.get("branch1", "path/to/file.py")
        assert result == (3, "abc123")

    def test_get_nonexistent(self):
        index = DedupIndex()
        assert index.get("branch", "no-such") is None

    def test_invalidate_single(self):
        index = DedupIndex()
        index.set("branch", "path", 1, "hash1")
        index.invalidate("branch", "path")
        assert index.get("branch", "path") is None

    def test_invalidate_branch(self):
        index = DedupIndex()
        index.set("b1", "a.py", 1, "h1")
        index.set("b1", "b.py", 2, "h2")
        index.set("b2", "a.py", 1, "h3")
        index.invalidate_branch("b1")
        assert index.get("b1", "a.py") is None
        assert index.get("b1", "b.py") is None
        assert index.get("b2", "a.py") == (1, "h3")


class TestContextCache:
    def test_set_and_get(self):
        cache = ContextCache()
        entry = ContextCacheEntry(
            branch="test", turn_n=5, messages=[{"role": "user", "content": "hello"}]
        )
        cache.set(entry)
        result = cache.get("test", 5)
        assert result is not None
        assert result.branch == "test"
        assert result.turn_n == 5
        assert result.messages == [{"role": "user", "content": "hello"}]

    def test_get_nonexistent(self):
        cache = ContextCache()
        assert cache.get("branch", 99) is None

    def test_invalidate(self):
        cache = ContextCache()
        cache.set(ContextCacheEntry("b1", 1, [{"role": "user", "content": "a"}]))
        cache.set(ContextCacheEntry("b1", 2, [{"role": "user", "content": "b"}]))
        cache.set(ContextCacheEntry("b2", 1, [{"role": "user", "content": "c"}]))
        cache.invalidate("b1")
        assert cache.get("b1", 1) is None
        assert cache.get("b1", 2) is None
        assert cache.get("b2", 1) is not None


class TestContextAssembler:
    def test_assemble_basic(self, config: SessionConfig):
        session = Session.create("assemble-basic", config)
        session.append_user_turn("user message 1")
        session.append_assistant_turn("response 1", model="gpt-4")

        assembler = ContextAssembler(
            log_repo=session.log_repo,
            config=config,
        )

        messages = assembler.assemble(session.branch)

        assert len(messages) == 3
        assert messages[0]["role"] == "system"
        assert "Session started at" in messages[0]["content"]
        assert messages[1] == {"role": "user", "content": "user message 1"}
        assert messages[2] == {"role": "assistant", "content": "response 1"}

    def test_assemble_with_tool_calls(self, config: SessionConfig):
        session = Session.create("assemble-tools", config)
        session.append_user_turn("read files")
        session.append_tool_call_turn("read_file", {"path": "foo.py"})
        session.append_tool_result_turn("read_file", "file content here")
        session.append_assistant_turn("done reading", model="gpt-4")

        assembler = ContextAssembler(
            log_repo=session.log_repo,
            config=config,
        )

        messages = assembler.assemble(session.branch)

        assert len(messages) == 5
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"
        assert len(messages[2]["tool_calls"]) == 1
        assert messages[2]["tool_calls"][0]["function"]["name"] == "read_file"
        assert json.loads(
            messages[2]["tool_calls"][0]["function"]["arguments"]
        ) == {"path": "foo.py"}
        assert messages[3]["role"] == "tool"
        assert messages[3]["content"] == "file content here"
        assert messages[4]["role"] == "assistant"

    def test_tool_call_result_ids_match(self, config: SessionConfig):
        session = Session.create("id-match", config)
        session.append_tool_call_turn("read_file", {"path": "f.py"})
        session.append_tool_result_turn("read_file", "content")

        assembler = ContextAssembler(
            log_repo=session.log_repo,
            config=config,
        )

        messages = assembler.assemble(session.branch)
        tool_call_msg = messages[1]
        tool_result_msg = messages[2]

        call_id = tool_call_msg["tool_calls"][0]["id"]
        result_call_id = tool_result_msg["tool_call_id"]
        assert call_id == result_call_id

    def test_cache_hit(self, config: SessionConfig):
        session = Session.create("cache-hit", config)
        session.append_user_turn("hello")

        assembler = ContextAssembler(
            log_repo=session.log_repo,
            config=config,
        )

        first = assembler.assemble(session.branch, up_to_turn=1)
        second = assembler.assemble(session.branch, up_to_turn=1)

        assert first == second
        assert first is second

    def test_cache_invalidate(self, config: SessionConfig):
        session = Session.create("cache-inv", config)
        session.append_user_turn("msg")

        assembler = ContextAssembler(
            log_repo=session.log_repo,
            config=config,
        )

        first = assembler.assemble(session.branch, up_to_turn=1)
        assembler.cache.invalidate(session.branch)
        second = assembler.assemble(session.branch, up_to_turn=1)

        assert first == second
        assert first is not second

    def test_budget_truncation(self, config: SessionConfig):
        session = Session.create("budget-test", config)
        session.append_user_turn("A" * 100)
        session.append_assistant_turn("B" * 100, model="gpt-4")
        session.append_user_turn("C" * 100)

        assembler = ContextAssembler(
            log_repo=session.log_repo,
            config=config,
        )

        messages_no_budget = assembler.assemble(session.branch, budget_tokens=None)
        messages_with_budget = assembler.assemble(session.branch, budget_tokens=30)

        assert len(messages_with_budget) < len(messages_no_budget)

    def test_budget_preserves_newest(self, config: SessionConfig):
        session = Session.create("budget-newest", config)
        session.append_user_turn("old message here")
        session.append_user_turn("middle message here")
        session.append_user_turn("new message here")

        assembler = ContextAssembler(
            log_repo=session.log_repo,
            config=config,
        )

        messages = assembler.assemble(session.branch, budget_tokens=15)
        assert len(messages) >= 1
        newest_content = messages[-1]["content"]
        assert "new message here" in newest_content

    def test_dedup_repeated_file_read(self, config: SessionConfig):
        session = Session.create("dedup-test", config)
        session.append_user_turn("read same file twice")
        session.append_tool_call_turn("read_file", {"path": "foo.py"})
        session.append_tool_result_turn("read_file", "same content")
        session.append_tool_call_turn("read_file", {"path": "foo.py"})
        session.append_tool_result_turn("read_file", "same content")
        session.append_assistant_turn("done", model="gpt-4")

        assembler = ContextAssembler(
            log_repo=session.log_repo,
            config=config,
        )

        messages = assembler.assemble(session.branch)

        tool_msgs = [m for m in messages if m["role"] == "tool"]
        assert len(tool_msgs) == 2
        assert tool_msgs[0]["content"] == "same content"
        assert "content unchanged" in tool_msgs[1]["content"]

    def test_dedup_different_content_not_deduplicated(self, config: SessionConfig):
        session = Session.create("dedup-diff", config)
        session.append_tool_call_turn("read_file", {"path": "foo.py"})
        session.append_tool_result_turn("read_file", "first content")
        session.append_tool_call_turn("read_file", {"path": "foo.py"})
        session.append_tool_result_turn("read_file", "different content")

        assembler = ContextAssembler(
            log_repo=session.log_repo,
            config=config,
        )

        messages = assembler.assemble(session.branch)

        tool_msgs = [m for m in messages if m["role"] == "tool"]
        assert len(tool_msgs) == 2
        assert tool_msgs[0]["content"] == "first content"
        assert tool_msgs[1]["content"] == "different content"

    def test_assemble_up_to_turn(self, config: SessionConfig):
        session = Session.create("up-to", config)
        session.append_user_turn("first")
        session.append_assistant_turn("second", model="gpt-4")
        session.append_user_turn("third")

        assembler = ContextAssembler(
            log_repo=session.log_repo,
            config=config,
        )

        messages = assembler.assemble(session.branch, up_to_turn=1)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_up_to_turn_zero_includes_system_only(self, config: SessionConfig):
        session = Session.create("up-to-zero", config)
        session.append_user_turn("msg")

        assembler = ContextAssembler(
            log_repo=session.log_repo,
            config=config,
        )

        messages = assembler.assemble(session.branch, up_to_turn=0)
        assert len(messages) == 1
        assert messages[0]["role"] == "system"

    def test_rag_results_prepended(self, config: SessionConfig):
        session = Session.create("rag-test", config)
        session.append_user_turn("query")

        assembler = ContextAssembler(
            log_repo=session.log_repo,
            config=config,
        )

        rag_results = [
            {"type": "doc", "score": 0.95, "content": "relevant doc content"},
        ]

        messages = assembler.assemble(session.branch, rag_results=rag_results)
        assert len(messages) == 3
        assert messages[0]["role"] == "system"
        assert "relevant doc" in messages[0]["content"]
        assert "score: 0.950" in messages[0]["content"]

    def test_summary_turns_excluded(self, config: SessionConfig):
        session = Session.create("summary-excl", config)
        sha1 = session.append_user_turn("first message")
        sha2 = session.append_assistant_turn("response", model="gpt-4")
        session.append_summary_turn(
            "summary content", summarizes=[sha1], model="gpt-4"
        )

        assembler = ContextAssembler(
            log_repo=session.log_repo,
            config=config,
        )

        messages = assembler.assemble(session.branch)
        contents = [m["content"] for m in messages]

        assert "summary content" not in contents
        assert "first message" not in contents
        assert "response" in contents

    def test_empty_session(self, config: SessionConfig):
        session = Session.create("empty", config)

        assembler = ContextAssembler(
            log_repo=session.log_repo,
            config=config,
        )

        messages = assembler.assemble(session.branch)
        assert len(messages) == 1
        assert messages[0]["role"] == "system"

    def test_dedup_index_rebuild(self, config: SessionConfig):
        session = Session.create("rebuild-idx", config)
        session.append_tool_call_turn("read_file", {"path": "foo.py"})
        session.append_tool_result_turn("read_file", "file content for foo")
        session.append_tool_call_turn("read_file", {"path": "bar.py"})
        session.append_tool_result_turn("read_file", "file content for bar")

        index = DedupIndex()
        index.rebuild_from_log(session.log_repo, session.branch)

        foo_entry = index.get(session.branch, "foo.py")
        assert foo_entry is not None
        assert foo_entry[0] == 1

        bar_entry = index.get(session.branch, "bar.py")
        assert bar_entry is not None
        assert bar_entry[0] == 3

    def test_compute_summary(self, config: SessionConfig):
        session = Session.create("compute-summary", config)
        sha1 = session.append_user_turn("first")
        sha2 = session.append_assistant_turn("second", model="gpt-4")

        assembler = ContextAssembler(
            log_repo=session.log_repo,
            config=config,
        )

        summary = assembler.compute_summary(
            session.branch, sha1, sha2, "summarized content"
        )

        assert summary.role == TurnRole.summary
        assert summary.content == "summarized content"
        assert isinstance(summary.summarizes, list)
        assert sha1 in summary.summarizes
        assert sha2 in summary.summarizes
