from __future__ import annotations

import json
import operator
import re
from pathlib import Path
from typing import Any


class QueryBuilder:
    def __init__(self, repo_root: str = ".") -> None:
        self._index_path = Path(repo_root) / ".gitlord" / "index.json"
        self._turns: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self._index_path.exists():
            return
        try:
            with open(self._index_path) as f:
                index = json.load(f)
            for session in index.get("sessions", {}).values():
                self._turns.extend(session.get("turns", []))
        except (json.JSONDecodeError, OSError):
            pass

    def where(self, clause: str) -> QueryBuilder:
        filtered = [t for t in self._turns if self._eval_clause(t, clause)]
        q = QueryBuilder.__new__(QueryBuilder)
        q._index_path = self._index_path
        q._turns = filtered
        return q

    def group_by(self, field: str) -> dict[str, list[dict[str, Any]]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for turn in self._turns:
            key = str(turn.get(field, ""))
            groups.setdefault(key, []).append(turn)
        return groups

    def sum(self, field: str) -> float:
        return sum(float(t.get(field, 0) or 0) for t in self._turns)

    def count(self) -> int:
        return len(self._turns)

    def avg(self, field: str) -> float:
        if not self._turns:
            return 0.0
        return self.sum(field) / len(self._turns)

    def collect(self) -> list[dict[str, Any]]:
        return list(self._turns)

    @staticmethod
    def _eval_clause(turn: dict, clause: str) -> bool:
        clause = clause.strip()

        m = re.match(r"^(.+)\s+is not null$", clause)
        if m:
            field = m.group(1).strip()
            return turn.get(field) is not None

        m = re.match(r"^(.+)\s+is null$", clause)
        if m:
            field = m.group(1).strip()
            return turn.get(field) is None

        ops = {
            ">=": operator.ge,
            "<=": operator.le,
            "!=": operator.ne,
            "==": operator.eq,
            ">": operator.gt,
            "<": operator.lt,
        }
        for sym, op in ops.items():
            if sym in clause:
                parts = clause.split(sym, 1)
                field = parts[0].strip()
                val = parts[1].strip()
                turn_val = turn.get(field)
                if turn_val is None:
                    return False
                try:
                    return op(float(turn_val), float(val))
                except (ValueError, TypeError):
                    return op(str(turn_val), str(val))

        return True
import re
from pathlib import Path
from typing import Any, Callable, Optional


OP_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*(>=|<=|==|!=|>|<|is not|null|is)\s*(.+)$")


class QueryError(Exception):
    pass


class TurnQuery:
    def __init__(self, index_data: dict[str, Any] | None = None):
        self._filters: list[Callable[[dict[str, Any]], bool]] = []
        self._groups: dict[str, list[dict[str, Any]]] = {}
        self._group_by_fields: list[str] = []
        self._results: list[dict[str, Any]] = []
        self._loaded: bool = False

        if index_data is not None:
            self._load(index_data)

    def _load(self, index_data: dict[str, Any]) -> None:
        self._results = []
        sessions = index_data.get("sessions", {})
        for session_id, sdata in sessions.items():
            for turn in sdata.get("turns", []):
                entry = {
                    "session_id": session_id,
                    "turn": turn.get("turn", 0),
                    "role": turn.get("role", ""),
                    "tags": turn.get("tags", []),
                    "tool": turn.get("tool"),
                    "sha": turn.get("sha", ""),
                    "tokens": turn.get("tokens", 0),
                    "tokens_in": turn.get("tokens_in", 0),
                    "tokens_out": turn.get("tokens_out", 0),
                    "cost": turn.get("cost", 0.0),
                    "error": turn.get("error"),
                    "turn_id": turn.get("turn_id", ""),
                    "agent_id": turn.get("agent_id", ""),
                    "parent_agent_id": turn.get("parent_agent_id"),
                }
                self._results.append(entry)
        self._loaded = True

    def load(self, path: str | Path) -> TurnQuery:
        with open(path) as f:
            data = json.load(f)
        self._load(data)
        return self

    def _evaluate(self, entry: dict[str, Any], expr: str) -> bool:
        m = OP_RE.match(expr.strip())
        if not m:
            raise QueryError(f"Invalid where expression: {expr!r}")

        field = m.group(1)
        op = m.group(2).strip()
        val_str = m.group(3).strip()

        actual = entry.get(field)

        if op == "is" and val_str.lower() == "null":
            return actual is None
        if op == "is not" and val_str.lower() == "null":
            return actual is not None

        try:
            val: Any = int(val_str)
        except (ValueError, TypeError):
            try:
                val = float(val_str)
            except (ValueError, TypeError):
                val = val_str.strip("\"'")

        if op == ">":
            return actual is not None and float(actual) > float(val)
        elif op == "<":
            return actual is not None and float(actual) < float(val)
        elif op == ">=":
            return actual is not None and float(actual) >= float(val)
        elif op == "<=":
            return actual is not None and float(actual) <= float(val)
        elif op == "==":
            return actual == val
        elif op == "!=":
            return actual != val

        return True

    def where(self, expression: str) -> TurnQuery:
        def _filter(entry: dict[str, Any]) -> bool:
            return self._evaluate(entry, expression)
        self._filters.append(_filter)
        return self

    def group_by(self, field: str) -> TurnQuery:
        self._group_by_fields.append(field)
        return self

    def sum(self, field: str) -> list[dict[str, Any]]:
        data = self._apply_filters()
        if not self._group_by_fields:
            total = sum(self._safe_float(d.get(field, 0)) for d in data)
            return [{"field": field, "sum": total}]

        groups: dict[str, dict[str, Any]] = {}
        for entry in data:
            key_parts = [str(entry.get(f, "")) for f in self._group_by_fields]
            key = "|".join(key_parts)
            if key not in groups:
                group_key = {f: entry.get(f) for f in self._group_by_fields}
                groups[key] = {**group_key, field: 0.0, "_count": 0}
            groups[key][field] = groups[key].get(field, 0.0) + self._safe_float(
                entry.get(field, 0)
            )
            groups[key]["_count"] = groups[key].get("_count", 0) + 1

        result = []
        for g in groups.values():
            row = {k: v for k, v in g.items() if k != "_count"}
            row["count"] = g["_count"]
            result.append(row)
        return result

    def count(self) -> int:
        return len(self._apply_filters())

    def avg(self, field: str) -> list[dict[str, Any]]:
        data = self._apply_filters()
        if not data:
            return []
        sums = self.sum(field)
        total_count = len(data)

        if not self._group_by_fields:
            return [{"field": field, "avg": sums[0]["sum"] / total_count if total_count > 0 else 0.0}]

        grouped = self._apply_filters()
        groups: dict[str, dict[str, Any]] = {}
        for entry in grouped:
            key_parts = [str(entry.get(f, "")) for f in self._group_by_fields]
            key = "|".join(key_parts)
            if key not in groups:
                group_key = {f: entry.get(f) for f in self._group_by_fields}
                groups[key] = {**group_key, field: 0.0, "_count": 0}
            groups[key][field] = groups[key].get(field, 0.0) + self._safe_float(
                entry.get(field, 0)
            )
            groups[key]["_count"] += 1

        result = []
        for g in groups.values():
            row = {k: v for k, v in g.items() if k != "_count"}
            row["avg"] = g[field] / g["_count"] if g["_count"] > 0 else 0.0
            row["count"] = g["_count"]
            result.append(row)
        return result

    def min(self, field: str) -> list[dict[str, Any]]:
        data = self._apply_filters()
        if not data:
            return []

        grouped = self._group(data)
        result = []
        for g in grouped:
            vals = [self._safe_float(e.get(field, 0)) for e in g["_entries"]]
            row = {k: v for k, v in g.items() if k != "_entries"}
            row["min"] = min(vals) if vals else 0.0
            row["count"] = len(vals)
            result.append(row)
        return result

    def max(self, field: str) -> list[dict[str, Any]]:
        data = self._apply_filters()
        if not data:
            return []

        grouped = self._group(data)
        result = []
        for g in grouped:
            vals = [self._safe_float(e.get(field, 0)) for e in g["_entries"]]
            row = {k: v for k, v in g.items() if k != "_entries"}
            row["max"] = max(vals) if vals else 0.0
            row["count"] = len(vals)
            result.append(row)
        return result

    def list(self) -> list[dict[str, Any]]:
        return self._apply_filters()

    def _apply_filters(self) -> list[dict[str, Any]]:
        if not self._loaded:
            return []
        data = self._results
        for f in self._filters:
            data = [e for e in data if f(e)]
        return data

    def _group(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self._group_by_fields:
            return [{"_entries": data}]

        groups: dict[str, dict[str, Any]] = {}
        for entry in data:
            key_parts = [str(entry.get(f, "")) for f in self._group_by_fields]
            key = "|".join(key_parts)
            if key not in groups:
                group_key = {f: entry.get(f) for f in self._group_by_fields}
                groups[key] = {**group_key, "_entries": []}
            groups[key]["_entries"].append(entry)

        return list(groups.values())

    @staticmethod
    def _safe_float(v: Any) -> float:
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.0
