from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Optional

try:
    import ulid as _ulid_mod

    def _new_ulid() -> str:
        return str(_ulid_mod.new())

except ImportError:

    def _new_ulid() -> str:
        import uuid
        return uuid.uuid4().hex[:26]


from gitlord.schemas import GitlordError, SessionConfig, Turn, TurnRole
from gitlord.git import GitRepo
from gitlord.session import Session


class SubagentManager:
    def __init__(
        self,
        log_repo: GitRepo,
        workspace_repo: GitRepo,
        config: SessionConfig,
        session_id: str,
        on_complete: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.log_repo = log_repo
        self.workspace_repo = workspace_repo
        self.config = config
        self.session_id = session_id
        self._queues: dict[str, deque[dict[str, Any]]] = {}
        self._on_complete = on_complete
        self._active_subagents: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _subagent_branch(parent_branch: str, subagent_id: str) -> str:
        if parent_branch.startswith("refs/agents/sub/"):
            return f"{parent_branch}/{subagent_id}"
        session_id = parent_branch.removeprefix("refs/agents/")
        return f"refs/agents/sub/{session_id}/{subagent_id}"

    def spawn(
        self,
        parent_branch: str,
        parent_agent_id: str,
    ) -> tuple[str, str]:
        if self._get_depth(parent_branch) >= self.config.agent.max_depth:
            raise GitlordError(
                f"Max agent depth {self.config.agent.max_depth} reached"
            )

        subagent_id = _new_ulid()
        branch = self._subagent_branch(parent_branch, subagent_id)

        parent_sha = self.log_repo.read_ref(parent_branch)
        if not parent_sha:
            raise GitlordError(f"Parent branch {parent_branch} has no commits")

        with self._lock:
            active_for_parent = [
                s
                for s in self._active_subagents.values()
                if s["parent_branch"] == parent_branch
            ]
            queue = self._queues.get(parent_branch)
            pending = len(queue) if queue else 0
            capacity = max(1, len(active_for_parent))
            if pending >= capacity:
                raise GitlordError(
                    f"Parent queue at capacity ({pending} pending >= {capacity} capacity)"
                )

        self.log_repo.update_ref(branch, parent_sha)

        with self._lock:
            self._active_subagents[subagent_id] = {
                "branch": branch,
                "parent_branch": parent_branch,
                "parent_agent_id": parent_agent_id,
                "spawned_at": datetime.now(timezone.utc),
                "final_sha": None,
            }

        system_content = (
            f"Subagent {subagent_id} spawned from {parent_agent_id} "
            f"at {datetime.now(timezone.utc).isoformat()}"
        )
        session = Session(
            self.log_repo, self.workspace_repo, self.config, self.session_id
        )
        session._branch = branch
        session.append_turn(
            Turn(
                turn=0,
                role=TurnRole.system,
                content=system_content,
                agent_id=f"{parent_agent_id}/{subagent_id}",
                parent_agent_id=parent_agent_id,
            )
        )

        return subagent_id, branch

    def complete(
        self,
        subagent_id: str,
        final_sha: str,
    ) -> None:
        with self._lock:
            info = self._active_subagents.get(subagent_id)
            if not info:
                raise GitlordError(
                    f"Subagent {subagent_id} not found or already completed"
                )

            parent_branch = info["parent_branch"]
            info["final_sha"] = final_sha
            self._active_subagents.pop(subagent_id)

            queue = self._queues.setdefault(parent_branch, deque())
            queue.append({
                "subagent_id": subagent_id,
                "final_sha": final_sha,
                "branch": info["branch"],
            })

        if self._on_complete:
            self._on_complete(subagent_id, final_sha)

    def drain_queue(self, parent_branch: str) -> None:
        with self._lock:
            queue = self._queues.get(parent_branch)
            if not queue:
                return
            items = list(queue)
            queue.clear()

        if not items:
            return

        session = Session(
            self.log_repo, self.workspace_repo, self.config, self.session_id
        )
        session._branch = parent_branch
        keep_branches = self.config.agent.keep_subagent_branches

        for item in items:
            turn = Turn(
                turn=session._next_turn_number(),
                role=TurnRole.tool_result,
                content=f"Subagent {item['subagent_id']} completed",
                agent_id=parent_branch.split("/")[-1],
                parent_agent_id=None,
                tool_name="subagent",
                tool_output=json.dumps({
                    "subagent_id": item["subagent_id"],
                    "final_sha": item["final_sha"],
                }),
                tags=["subagent_complete"],
            )
            session._commit_turn(turn, subagent_result=item["final_sha"])

            if not keep_branches:
                self.log_repo.delete_ref(item["branch"])

    def trim(
        self,
        session_id: str | None = None,
        all_sessions: bool = False,
        keep_active: bool = True,
    ) -> int:
        if session_id and not all_sessions:
            prefix = f"refs/agents/sub/{session_id}/"
        else:
            prefix = "refs/agents/"

        refs = self.log_repo.list_refs(prefix)
        active_branches: set[str] = set()
        if keep_active:
            with self._lock:
                for info in self._active_subagents.values():
                    active_branches.add(info["branch"])

        count = 0
        for ref in refs:
            if ref in active_branches:
                continue
            if ref.count("/") <= 3:
                continue
            self.log_repo.delete_ref(ref)
            count += 1

        return count

    def _get_depth(self, branch: str) -> int:
        parts = branch.split("/")
        agent_parts = [p for p in parts if p not in ("refs", "agents")]
        if agent_parts and agent_parts[0] == "sub":
            agent_parts = agent_parts[1:]
        return max(0, len(agent_parts) - 1)

    def is_active(self, subagent_id: str) -> bool:
        with self._lock:
            return subagent_id in self._active_subagents
