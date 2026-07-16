from __future__ import annotations

from typing import Any

import pytest

from gitlord.rag import VectorIndex, ChromaDBError
from gitlord.schemas import ChunkingConfig

try:
    import chromadb  # noqa: F401

    HAS_CHROMADB = True
except Exception:
    HAS_CHROMADB = False


class TestChunking:
    @pytest.fixture
    def index(self) -> VectorIndex:
        i = VectorIndex.__new__(VectorIndex)
        i.chunking = ChunkingConfig()
        return i

    def test_empty_text(self, index: VectorIndex):
        assert index._chunk("") == [""]

    def test_short_text(self, index: VectorIndex):
        assert index._chunk("short") == ["short"]

    def test_exact_size(self, index: VectorIndex):
        index.chunking = ChunkingConfig(chunk_size=5)
        assert index._chunk("12345") == ["12345"]

    def test_simple_split(self, index: VectorIndex):
        index.chunking = ChunkingConfig(chunk_size=5, chunk_overlap=0)
        result = index._chunk("0123456789")
        assert result == ["01234", "56789"]

    def test_overlap(self, index: VectorIndex):
        index.chunking = ChunkingConfig(chunk_size=10, chunk_overlap=3)
        result = index._chunk("0123456789abcdefghij")
        assert len(result) == 3
        assert result[0] == "0123456789"
        assert result[1] == "789abcdefg"
        assert result[2] == "efghij"

    def test_chunk_size_zero_returns_full(self, index: VectorIndex):
        index.chunking = ChunkingConfig(chunk_size=0)
        assert index._chunk("long text here") == ["long text here"]

    def test_chunk_size_negative_returns_full(self, index: VectorIndex):
        index.chunking = ChunkingConfig(chunk_size=-1)
        assert index._chunk("some text") == ["some text"]


class TestFormatResults:
    @pytest.fixture
    def index(self) -> VectorIndex:
        return VectorIndex.__new__(VectorIndex)

    def test_empty_dict(self, index: VectorIndex):
        assert index._format_results({}) == []

    def test_none(self, index: VectorIndex):
        assert index._format_results(None) == []

    def test_no_ids(self, index: VectorIndex):
        assert index._format_results({"ids": []}) == []

    def test_full_results(self, index: VectorIndex):
        raw = {
            "ids": [["id1", "id2"]],
            "documents": [["doc1", "doc2"]],
            "distances": [[0.1, 0.2]],
            "metadatas": [[{"key": "a"}, {"key": "b"}]],
        }
        result = index._format_results(raw)
        assert len(result) == 2
        assert result[0]["id"] == "id1"
        assert result[0]["content"] == "doc1"
        assert result[0]["score"] == 0.1
        assert result[0]["metadata"]["key"] == "a"
        assert result[1]["id"] == "id2"

    def test_no_metadata(self, index: VectorIndex):
        raw = {
            "ids": [["id1"]],
            "documents": [["doc1"]],
            "distances": [[0.5]],
        }
        result = index._format_results(raw)
        assert "metadata" not in result[0]

    def test_no_documents(self, index: VectorIndex):
        raw = {
            "ids": [["id1"]],
            "distances": [[0.5]],
        }
        result = index._format_results(raw)
        assert result[0]["content"] == ""


@pytest.mark.skipif(not HAS_CHROMADB, reason="chromadb not available")
class TestVectorIndexChromaDB:
    def test_create_index(self, tmp_path):
        index = VectorIndex(
            collection_name="test-create",
            persist_directory=str(tmp_path / ".chroma"),
        )
        assert index.count() == 0

    def test_index_turn_adds_chunks(self, tmp_path):
        index = VectorIndex(
            collection_name="test-turn",
            persist_directory=str(tmp_path / ".chroma"),
        )
        n = index.index_turn("sha1", "agent-1", 1, "Hello world this is a test")
        assert n > 0
        assert index.count() == n

    def test_index_turn_and_query_roundtrip(self, tmp_path):
        index = VectorIndex(
            collection_name="test-roundtrip",
            persist_directory=str(tmp_path / ".chroma"),
        )
        index.index_turn("abc123", "agent-1", 1, "The quick brown fox jumps")
        results = index.query("quick fox", n_results=5)
        assert len(results) > 0
        assert results[0]["metadata"]["sha"] == "abc123"
        assert results[0]["metadata"]["agent_id"] == "agent-1"

    def test_query_by_type(self, tmp_path):
        index = VectorIndex(
            collection_name="test-by-type",
            persist_directory=str(tmp_path / ".chroma"),
        )
        index.index_turn("sha1", "agent-1", 1, "programming content here")
        index.index_doc("doc1", "python programming content", "docs")

        results = index.query_by_type("programming", "agent_turn")
        assert len(results) > 0
        for r in results:
            assert r["metadata"]["type"] == "agent_turn"

    def test_query_mmr(self, tmp_path):
        index = VectorIndex(
            collection_name="test-mmr",
            persist_directory=str(tmp_path / ".chroma"),
        )
        index.index_turn(
            "sha1", "agent-1", 1, "The quick brown fox jumps over the lazy dog"
        )
        index.index_turn(
            "sha2", "agent-1", 2, "A quick brown dog jumps over a lazy fox"
        )
        index.index_turn(
            "sha3", "agent-1", 3, "Python is a programming language for coding"
        )

        results = index.query_mmr("quick brown fox", n_results=3, diversity=0.7)
        assert len(results) > 0
        assert results[0]["metadata"]["type"] == "agent_turn"

    def test_hybrid_search(self, tmp_path):
        index = VectorIndex(
            collection_name="test-hybrid",
            persist_directory=str(tmp_path / ".chroma"),
        )
        index.index_turn("sha1", "agent-1", 1, "hybrid search test content")
        results = index.hybrid_search("hybrid search", n_results=5)
        assert len(results) > 0
        assert results[0]["metadata"]["sha"] == "sha1"

    def test_index_doc(self, tmp_path):
        index = VectorIndex(
            collection_name="test-doc",
            persist_directory=str(tmp_path / ".chroma"),
        )
        n = index.index_doc("mydoc", "document content here", "wiki")
        assert n > 0
        results = index.query("document", filter_by={"doc_id": "mydoc"})
        assert len(results) > 0

    def test_delete_turn(self, tmp_path):
        index = VectorIndex(
            collection_name="test-delete",
            persist_directory=str(tmp_path / ".chroma"),
        )
        index.index_turn("del-sha", "agent-1", 1, "content to delete")
        assert index.count() > 0
        index.delete_turn("del-sha")
        assert index.count() == 0

    def test_delete_doc(self, tmp_path):
        index = VectorIndex(
            collection_name="test-delete-doc",
            persist_directory=str(tmp_path / ".chroma"),
        )
        index.index_doc("doc-to-delete", "some content", "wiki")
        assert index.count() > 0
        index.delete_doc("doc-to-delete")
        assert index.count() == 0

    def test_clear(self, tmp_path):
        index = VectorIndex(
            collection_name="test-clear",
            persist_directory=str(tmp_path / ".chroma"),
        )
        index.index_turn("sha1", "agent-1", 1, "content a")
        index.index_turn("sha2", "agent-1", 2, "content b")
        assert index.count() > 0
        index.clear()
        assert index.count() == 0

    def test_query_with_filter_by(self, tmp_path):
        index = VectorIndex(
            collection_name="test-filter",
            persist_directory=str(tmp_path / ".chroma"),
        )
        index.index_turn("sha1", "agent-1", 1, "unique content for filtering")
        results = index.query("unique", filter_by={"sha": "sha1"})
        assert len(results) > 0
        assert results[0]["metadata"]["sha"] == "sha1"
