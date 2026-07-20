from __future__ import annotations

import json
from pathlib import Path

import pytest

from gitlord.query import TurnQuery


SAMPLE_INDEX = {
    "built_at": "2026-07-20T00:00:00",
    "sessions": {
        "session-a": {
            "branch": "refs/agents/session-a",
            "turns": [
                {"sha": "aaa1", "turn": 0, "role": "system", "tags": [], "tokens": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0, "turn_id": "t0", "agent_id": "session-a"},
                {"sha": "aaa2", "turn": 1, "role": "user", "tags": [], "tokens": 50, "tokens_in": 0, "tokens_out": 0, "cost": 0.0, "turn_id": "t1", "agent_id": "session-a"},
                {"sha": "aaa3", "turn": 2, "role": "assistant", "tags": [], "tokens": 250, "tokens_in": 100, "tokens_out": 150, "cost": 0.025, "turn_id": "t2", "agent_id": "session-a"},
                {"sha": "aaa4", "turn": 3, "role": "tool_call", "tags": ["err"], "tokens": 30, "tokens_in": 0, "tokens_out": 0, "cost": 0.0, "turn_id": "t3", "agent_id": "session-a", "error": "tool_call_error"},
                {"sha": "aaa5", "turn": 4, "role": "tool_result", "tags": [], "tokens": 100, "tokens_in": 0, "tokens_out": 0, "cost": 0.0, "turn_id": "t4", "agent_id": "session-a"},
                {"sha": "aaa6", "turn": 5, "role": "assistant", "tags": [], "tokens": 500, "tokens_in": 200, "tokens_out": 300, "cost": 0.05, "turn_id": "t5", "agent_id": "session-a"},
            ],
            "subagents": [],
        },
        "session-b": {
            "branch": "refs/agents/session-b",
            "turns": [
                {"sha": "bbb1", "turn": 0, "role": "system", "tags": [], "tokens": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0, "turn_id": "t0", "agent_id": "session-b"},
                {"sha": "bbb2", "turn": 1, "role": "user", "tags": [], "tokens": 75, "tokens_in": 0, "tokens_out": 0, "cost": 0.0, "turn_id": "t1", "agent_id": "session-b"},
                {"sha": "bbb3", "turn": 2, "role": "assistant", "tags": [], "tokens": 300, "tokens_in": 150, "tokens_out": 150, "cost": 0.03, "turn_id": "t2", "agent_id": "session-b"},
            ],
            "subagents": [],
        },
    },
}


class TestTurnQueryBasics:
    def test_empty_query(self):
        q = TurnQuery()
        assert q.list() == []
        assert q.count() == 0

    def test_load_all(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = q.list()
        assert len(results) == 9

    def test_load_from_file(self, tmp_path: Path):
        path = tmp_path / "index.json"
        path.write_text(json.dumps(SAMPLE_INDEX))
        q = TurnQuery().load(str(path))
        assert q.count() == 9

    def test_count(self):
        q = TurnQuery(SAMPLE_INDEX)
        assert q.count() == 9


class TestTurnQueryWhere:
    def test_where_tokens_gt(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = q.where("tokens > 200").list()
        assert len(results) == 3
        roles = {r["role"] for r in results}
        assert roles == {"assistant"}

    def test_where_tokens_le(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = q.where("tokens <= 75").list()
        assert len(results) == 5

    def test_where_error_eq(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = q.where('error == tool_call_error').list()
        assert len(results) == 1
        assert results[0]["turn"] == 3
        assert results[0]["role"] == "tool_call"

    def test_where_error_is_not_null(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = q.where("error is not null").list()
        assert len(results) == 1
        assert results[0]["error"] == "tool_call_error"

    def test_where_role_eq(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = q.where('role == assistant').list()
        assert len(results) == 3

    def test_where_multiple_chain(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = q.where("tokens > 100").where("error is not null").list()
        assert len(results) == 0

    def test_where_tokens_gt_and_tokens_lt(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = q.where("tokens > 40").where("tokens < 200").list()
        assert len(results) == 3
        for r in results:
            assert r["turn"] in (1, 3, 4)

    def test_where_role_ne(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = q.where('role != system').list()
        assert len(results) == 7


class TestTurnQueryGroupBy:
    def test_group_by_role_count(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = q.group_by("role").sum("tokens")
        assert len(results) >= 3
        roles = {r["role"] for r in results}
        assert "system" in roles
        assert "user" in roles
        assert "assistant" in roles

    def test_group_by_role_sum(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = q.group_by("role").sum("tokens")
        asst = [r for r in results if r["role"] == "assistant"]
        assert len(asst) == 1
        assert asst[0]["tokens"] == 1050

    def test_group_by_role_avg(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = q.group_by("role").avg("tokens")
        asst = [r for r in results if r["role"] == "assistant"]
        assert len(asst) == 1
        assert asst[0]["count"] == 3

    def test_group_by_role_min(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = q.group_by("role").min("tokens")
        asst = [r for r in results if r["role"] == "assistant"]
        assert len(asst) == 1
        assert asst[0]["min"] == 250

    def test_group_by_role_max(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = q.group_by("role").max("tokens")
        asst = [r for r in results if r["role"] == "assistant"]
        assert len(asst) == 1
        assert asst[0]["max"] == 500

    def test_group_by_session_and_role(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = q.group_by("session_id").group_by("role").sum("cost")
        assert len(results) >= 6
        for r in results:
            assert "session_id" in r
            assert "role" in r
            assert "cost" in r


class TestTurnQuerySum:
    def test_sum_all(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = q.sum("tokens")
        assert len(results) == 1
        assert results[0]["sum"] == 1305

    def test_sum_cost(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = q.sum("cost")
        assert results[0]["sum"] == pytest.approx(0.105, rel=1e-3)

    def test_sum_cost_grouped(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = q.where("tokens > 100").group_by("session_id").sum("cost")
        assert len(results) == 2


class TestTurnQueryIntegration:
    def test_query_chain(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = (
            q.where("tokens > 100")
            .where("error is not null")
            .group_by("role")
            .sum("cost")
        )
        assert results == []

    def test_where_then_count(self):
        q = TurnQuery(SAMPLE_INDEX)
        count = q.where("tokens > 0").count()
        assert count == 7

    def test_no_results_after_filter(self):
        q = TurnQuery(SAMPLE_INDEX)
        results = q.where("tokens > 99999").list()
        assert results == []
