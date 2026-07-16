from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from gitlord.git import GitRepo, TURN_FILENAME_RE
from gitlord.rag import VectorIndex
from gitlord.schemas import Turn


class IndexBuilder:
    def __init__(
        self,
        log_repo: GitRepo,
        vector_index: Optional[VectorIndex] = None,
    ) -> None:
        self.log_repo = log_repo
        self.vector_index = vector_index

    def rebuild_json_index(self) -> dict[str, Any]:
        index: dict[str, Any] = {
            "sessions": {},
            "built_at": datetime.now(timezone.utc).isoformat(),
        }

        refs = self.log_repo.list_refs("refs/agents/")
        for ref in refs:
            session_info = self._build_session_index(ref)
            if session_info:
                session_id = ref.replace("refs/agents/", "", 1).split("/")[0]
                if session_id not in index["sessions"]:
                    index["sessions"][session_id] = {
                        "branch": f"refs/agents/{session_id}",
                        "turns": [],
                        "subagents": [],
                    }
                existing = index["sessions"][session_id]

                branch_path = ref.replace("refs/agents/", "", 1)
                if branch_path != session_id:
                    sub_path = branch_path[len(session_id) + 1:]
                    if sub_path and sub_path not in existing["subagents"]:
                        existing["subagents"].append(sub_path)

                existing["turns"].extend(session_info.get("turns", []))

        return index

    def rebuild_vector_index(self) -> int:
        if not self.vector_index:
            return 0

        self.vector_index.clear()
        total_chunks = 0

        refs = self.log_repo.list_refs("refs/agents/")
        for ref in refs:
            commits = self.log_repo.log_branch(ref, format="%H", reverse=True)
            for sha in commits:
                trailers = self.log_repo.parse_trailers(sha)
                if not trailers:
                    continue

                turn_filename = self.log_repo.get_turn_filename(sha)
                if not turn_filename:
                    continue

                raw = self.log_repo.get_turn_content(sha, turn_filename)
                try:
                    turn_data = json.loads(raw)
                    turn = Turn(**turn_data)
                except (json.JSONDecodeError, Exception):
                    continue

                content = turn.content or ""
                if not content.strip():
                    continue

                agent_id = turn.agent_id
                turn_num = turn.turn

                chunks = self.vector_index.index_turn(
                    sha=sha,
                    agent_id=agent_id,
                    turn=turn_num,
                    content=content,
                    tags=turn.tags,
                )
                total_chunks += chunks

        return total_chunks

    def _build_session_index(
        self, ref: str
    ) -> Optional[dict[str, Any]]:
        commits = self.log_repo.log_branch(ref, format="%H %s", reverse=True)
        turns = []

        for entry in commits:
            parts = entry.split(None, 1)
            sha = parts[0]
            summary = parts[1] if len(parts) > 1 else ""

            trailers = self.log_repo.parse_trailers(sha)
            if not trailers:
                continue

            turn_entry: dict[str, Any] = {
                "sha": sha,
                "turn": trailers.turn,
                "role": trailers.role,
                "tags": trailers.tags,
            }
            if trailers.tool:
                turn_entry["tool"] = trailers.tool
            turns.append(turn_entry)

        if not turns:
            return None

        return {
            "turns": turns,
        }

    def to_file(self, path: str = "index.json") -> dict[str, Any]:
        index = self.rebuild_json_index()
        with open(path, "w") as f:
            json.dump(index, f, indent=2)
        return index
