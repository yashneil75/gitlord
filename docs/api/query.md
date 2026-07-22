# Query API

```python
from gitlord import TurnQuery
```

In-memory query layer for filtering and aggregating turns.

## Usage

```python
query = session.query()
```

Or from a fresh `TurnQuery`:

```python
from gitlord import TurnQuery
query = TurnQuery().load(".gitlord/index.json")
```

## Filter Methods

### `where(**kwargs) -> TurnQuery`

Filter turns by metadata fields.

```python
# Filter by role
q = query.where(role="assistant")

# Filter by tags
q = query.where(tags=["err"])

# Filter by tool
q = query.where(tool="fetch")

# Filter by agent
q = query.where(agent_id="my-session/sub-001")
```

**Supported filter fields:** `role`, `tags`, `tool`, `agent_id`, `turn`

---

### `group_by(field) -> TurnQuery`

Group turns by a field.

```python
q = query.group_by("role")
```

## Aggregation Methods

### `sum(field) -> int`

Sum a numeric field across matching turns.

```python
total_tokens = query.where(role="assistant").sum("tokens_out")
```

---

### `avg(field) -> float`

Average a numeric field.

---

### `min(field) -> int | float`

Minimum value of a numeric field.

---

### `max(field) -> int | float`

Maximum value of a numeric field.

---

### `count() -> int`

Count matching turns.

```python
error_count = query.where(tags=["err"]).count()
```

---

### `list() -> list[dict]`

Return all matching turns as dicts.

```python
assistant_turns = query.where(role="assistant").list()
```

## Chaining

All filter and aggregation methods return `self` for chaining:

```python
result = (
    query
    .where(role="assistant")
    .where(tags=["retry"])
    .sum("tokens_out")
)
```
