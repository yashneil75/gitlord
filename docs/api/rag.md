# Vector Index (RAG) API

```python
from gitlord import VectorIndex
```

ChromaDB wrapper for semantic search across agent turns and external documents.

## Constructor

```python
VectorIndex(
    persist_directory: str = ".gitlord/chroma",
    collection_name: str = "gitlord",
    embedding_model: str | None = None,
)
```

## Methods

### `index_turn(sha, agent_id, turn, content, tags=None) -> None`

Index an agent turn's content.

```python
vi = VectorIndex()
vi.index_turn(
    sha="abc123",
    agent_id="my-session",
    turn=5,
    content="The weather in London is currently 15°C...",
    tags=["weather"],
)
```

---

### `index_doc(doc_id, content, source=None) -> None`

Index an external document.

```python
vi.index_doc(
    doc_id="readme-001",
    content=open("README.md").read(),
    source="file:///path/to/README.md",
)
```

---

### `query(query_text, n_results=5, filter_by=None) -> list[dict]`

Semantic search across indexed content.

```python
results = vi.query("weather in London", n_results=3)
# [{"id": "...", "type": "agent_turn", "score": 0.85, "sha": "abc123", ...}]
```

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query_text` | `str` | required | Search query |
| `n_results` | `int` | `5` | Max results to return |
| `filter_by` | `dict \| None` | `None` | ChromaDB where filter |

**Returns:** List of result dicts with `id`, `type`, `score`, and metadata

---

### `query_by_type(query_text, doc_type, n_results=5) -> list[dict]`

Convenience filter by document type (`"agent_turn"` or `"rag_doc"`).

---

### `delete_turn(sha) -> None`

Delete an indexed turn by commit SHA.

---

### `delete_doc(doc_id) -> None`

Delete an indexed document by ID.

---

### `count() -> int`

Return total number of indexed documents.

---

### `clear() -> None`

Clear the entire collection.

## Document Types

| Type | Metadata | Description |
|------|----------|-------------|
| `agent_turn` | `sha`, `agent_id`, `turn`, `tags` | Agent turn content |
| `rag_doc` | `doc_id`, `source` | External document |

## Chunking

Content is split into chunks before indexing:

- Default: 512 tokens per chunk, 50 token overlap
- Configurable via `ChunkingConfig` in `AgentConfig`
- Uses ChromaDB's native chunking where applicable

## Graceful Degradation

When `chromadb` is not installed, all methods raise `ImportError`. The framework continues to work without vector search.
