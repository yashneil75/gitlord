from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    HAS_CHROMADB = True
except ImportError:
    HAS_CHROMADB = False


from gitlord.schemas import ChunkingConfig


class ChromaDBError(Exception):
    pass


class VectorIndex:
    def __init__(
        self,
        collection_name: str = "gitlord",
        persist_directory: str = ".chroma",
        chunking: ChunkingConfig | None = None,
    ):
        if not HAS_CHROMADB:
            raise ChromaDBError("chromadb is not installed")

        self.chunking = chunking or ChunkingConfig()
        self._client = chromadb.PersistentClient(
            path=persist_directory,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
        )

    def index_turn(
        self,
        sha: str,
        agent_id: str,
        turn: int,
        content: str,
        tags: list[str] | None = None,
    ) -> int:
        chunks = self._chunk(content)
        metadatas = [
            {
                "type": "agent_turn",
                "sha": sha,
                "agent_id": agent_id,
                "turn": str(turn),
                "chunk": str(i),
                "indexed_at": datetime.now(timezone.utc).isoformat(),
                "tags": json.dumps(tags or []),
            }
            for i in range(len(chunks))
        ]

        ids = [f"{sha}_{i}" for i in range(len(chunks))]
        self._collection.add(
            documents=chunks,
            metadatas=metadatas,
            ids=ids,
        )
        return len(chunks)

    def index_doc(
        self,
        doc_id: str,
        content: str,
        source: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        chunks = self._chunk(content)
        base_md = {
            "type": "rag_doc",
            "doc_id": doc_id,
            "source": source,
            "indexed_at": datetime.now(timezone.utc).isoformat(),
        }
        if metadata:
            base_md.update(metadata)

        metadatas = [
            {
                **base_md,
                "chunk": str(i),
            }
            for i in range(len(chunks))
        ]

        ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
        self._collection.add(
            documents=chunks,
            metadatas=metadatas,
            ids=ids,
        )
        return len(chunks)

    def query(
        self,
        query_text: str,
        n_results: int = 5,
        filter_by: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        where = filter_by or {}

        try:
            results = self._collection.query(
                query_texts=[query_text],
                n_results=n_results,
                where=where if where else None,
            )
        except Exception as e:
            raise ChromaDBError(f"Query failed: {e}") from e

        return self._format_results(results)

    def query_by_type(
        self,
        query_text: str,
        doc_type: str,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        return self.query(
            query_text,
            n_results=n_results,
            filter_by={"type": doc_type},
        )

    def query_mmr(
        self,
        query_text: str,
        n_results: int = 5,
        diversity: float = 0.5,
        filter_by: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        where = filter_by or {}
        try:
            results = self._collection.query(
                query_texts=[query_text],
                n_results=n_results,
                where=where if where else None,
                lambda_mult=diversity,
            )
        except Exception as e:
            raise ChromaDBError(f"MMR query failed: {e}") from e
        return self._format_results(results)

    def hybrid_search(
        self,
        query_text: str,
        n_results: int = 5,
        filter_by: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        where = filter_by or {}
        try:
            results = self._collection.query(
                query_texts=[query_text],
                n_results=n_results,
                where=where if where else None,
            )
        except Exception as e:
            raise ChromaDBError(f"Hybrid search failed: {e}") from e
        return self._format_results(results)

    def delete_turn(self, sha: str) -> None:
        ids = self._collection.get(
            where={"sha": sha},
        ).get("ids", [])
        if ids:
            self._collection.delete(ids=ids)

    def delete_doc(self, doc_id: str) -> None:
        self._collection.delete(
            where={"doc_id": doc_id},
        )

    def count(self) -> int:
        return self._collection.count()

    def clear(self) -> None:
        self._collection.delete(where={})

    def _chunk(self, text: str) -> list[str]:
        if not text:
            return [""]

        size = self.chunking.chunk_size
        overlap = self.chunking.chunk_overlap

        if size <= 0:
            return [text]

        if len(text) <= size:
            return [text]

        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + size
            chunks.append(text[start:end])
            start += size - overlap

        return chunks

    def _format_results(self, raw: Any) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        if not raw or not raw.get("ids"):
            return results

        for i in range(len(raw["ids"][0])):
            entry: dict[str, Any] = {
                "id": raw["ids"][0][i],
                "content": raw["documents"][0][i] if raw.get("documents") else "",
                "score": raw["distances"][0][i] if raw.get("distances") else 0.0,
            }

            if raw.get("metadatas") and raw["metadatas"][0]:
                entry["metadata"] = raw["metadatas"][0][i]

            results.append(entry)

        return results
