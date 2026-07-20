import json
from pathlib import Path
from gitlord.query import QueryBuilder


def _make_index(tmp_path, turns):
    index = {"sessions": {"s1": {"branch": "refs/agents/s1", "turns": turns}}, "built_at": None}
    idx_dir = tmp_path / ".gitlord"
    idx_dir.mkdir(exist_ok=True)
    with open(idx_dir / "index.json", "w") as f:
        json.dump(index, f)
    return tmp_path


class TestQueryBuilder:
    def test_where_filters(self, tmp_path):
        _make_index(tmp_path, [
            {"sha": "a", "turn": 1, "role": "user", "tags": [], "tokens": 50},
            {"sha": "b", "turn": 2, "role": "assistant", "tags": [], "tokens": 200},
            {"sha": "c", "turn": 3, "role": "user", "tags": [], "tokens": 30},
        ])
        q = QueryBuilder(str(tmp_path))
        results = q.where("tokens > 100").collect()
        assert len(results) == 1
        assert results[0]["tokens"] == 200

    def test_where_error_not_null(self, tmp_path):
        _make_index(tmp_path, [
            {"sha": "a", "turn": 1, "role": "assistant", "tags": []},
            {"sha": "b", "turn": 2, "role": "assistant", "tags": [], "error": "timeout"},
        ])
        q = QueryBuilder(str(tmp_path))
        results = q.where("error is not null").collect()
        assert len(results) == 1
        assert results[0]["error"] == "timeout"

    def test_group_by(self, tmp_path):
        _make_index(tmp_path, [
            {"sha": "a", "turn": 1, "role": "user", "tags": []},
            {"sha": "b", "turn": 2, "role": "assistant", "tags": []},
            {"sha": "c", "turn": 3, "role": "user", "tags": []},
        ])
        q = QueryBuilder(str(tmp_path))
        groups = q.group_by("role")
        assert "user" in groups
        assert "assistant" in groups
        assert len(groups["user"]) == 2
        assert len(groups["assistant"]) == 1

    def test_sum(self, tmp_path):
        _make_index(tmp_path, [
            {"sha": "a", "turn": 1, "role": "user", "tags": [], "cost": 0.01},
            {"sha": "b", "turn": 2, "role": "assistant", "tags": [], "cost": 0.05},
        ])
        q = QueryBuilder(str(tmp_path))
        total = q.sum("cost")
        assert abs(total - 0.06) < 1e-9

    def test_count(self, tmp_path):
        _make_index(tmp_path, [
            {"sha": "a", "turn": 1, "role": "user", "tags": []},
            {"sha": "b", "turn": 2, "role": "assistant", "tags": []},
        ])
        q = QueryBuilder(str(tmp_path))
        assert q.count() == 2

    def test_chain_where(self, tmp_path):
        _make_index(tmp_path, [
            {"sha": "a", "turn": 1, "role": "user", "tags": [], "tokens": 50, "cost": 0.01},
            {"sha": "b", "turn": 2, "role": "assistant", "tags": [], "tokens": 200, "cost": 0.05},
            {"sha": "c", "turn": 3, "role": "user", "tags": [], "tokens": 300, "cost": 0.10},
        ])
        q = QueryBuilder(str(tmp_path))
        results = q.where("tokens > 100").where("cost < 0.08").collect()
        assert len(results) == 1
        assert results[0]["tokens"] == 200
