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
