from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Optional

try:
    from ulid import ULID

    def _new_ulid() -> str:
        return str(ULID())

except ImportError:

    def _new_ulid() -> str:
        import uuid
        return uuid.uuid4().hex[:26]


from gitlord.schemas import CommitTrailers, SessionConfig, Turn, TurnRole
from gitlord.git import GitRepo
from gitlord.session import Session


class SubagentQueue:
    def __init__(self, max_depth: int = 1):
        self._queue: deque[dict[str, Any]] = deque()
        self._max_depth = max_depth
        self._lock = threading.Lock()

    def enqueue(self, subagent_id: str, final_sha: str) -> None:
        with self._lock:
            self._queue.append({
                "subagent_id": subagent_id,
                "final_sha": final_sha,
            })

    def drain(self) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._queue)
            self._queue.clear()
            return items

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._queue)

    @property
    def is_full(self) -> bool:
        with self._lock:
            return len(self._queue) >= self._max_depth


class SubagentManager:
    def __init__(
        self,
        log_repo: GitRepo,
        workspace_repo: GitRepo,
        config: SessionConfig,
        session_id: str,
        on_complete: Optional[Callable[[str, str], None]] = None,
    ):
        self.log_repo = log_repo
        self.workspace_repo = workspace_repo
        self.config = config
        self.session_id = session_id
        self._queues: dict[str, SubagentQueue] = {}
        self._on_complete = on_complete
        self._active_subagents: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def spawn(
        self,
        parent_branch: str,
        parent_agent_id: str,
    ) -> tuple[str, str]:
        if self._get_depth(parent_branch) >= self.config.agent.max_depth:
            raise ValueError(
                f"Max agent depth {self.config.agent.max_depth} reached"
            )

        subagent_id = _new_ulid()
        branch = f"{parent_branch}/{subagent_id}"

        parent_sha = self.log_repo.read_ref(parent_branch)
        if not parent_sha:
            raise ValueError(f"Parent branch {parent_branch} has no commits")

        self.log_repo.update_ref(branch, parent_sha)

        with self._lock:
            self._active_subagents[subagent_id] = {
                "branch": branch,
                "parent_branch": parent_branch,
                "parent_agent_id": parent_agent_id,
                "spawned_at": datetime.now(timezone.utc),
                "final_sha": None,
            }

            parent_queue = self._queues.setdefault(
                parent_branch, SubagentQueue()
            )
            if parent_queue.is_full:
                del self._active_subagents[subagent_id]
                raise RuntimeError(
                    f"Parent queue at max depth ({parent_queue._max_depth})"
                )

        system_content = (
            f"Subagent {subagent_id} spawned from {parent_agent_id} "
            f"at {datetime.now(timezone.utc).isoformat()}"
        )
        session = Session(self.log_repo, self.workspace_repo, self.config, self.session_id)
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
                raise ValueError(f"Subagent {subagent_id} not found or already completed")

            parent_branch = info["parent_branch"]
            info["final_sha"] = final_sha
            self._active_subagents.pop(subagent_id)

            queue = self._queues.setdefault(parent_branch, SubagentQueue())
            queue.enqueue(subagent_id, final_sha)

        if self._on_complete:
            self._on_complete(subagent_id, final_sha)

    def drain_queue(self, parent_branch: str) -> None:
        queue = self._queues.get(parent_branch)
        if not queue:
            return

        items = queue.drain()
        if not items:
            return

        parent = Session(
            self.log_repo, self.workspace_repo, self.config, self.session_id
        )
        parent._branch = parent_branch

        for item in items:
            result_turn = Turn(
                turn=0,
                role=TurnRole.tool_result,
                content=f"Subagent {item['subagent_id']} completed",
                agent_id=parent_branch.split("/")[-1],
                tool_name="subagent",
                tool_output=json.dumps({
                    "subagent_id": item["subagent_id"],
                    "final_sha": item["final_sha"],
                }),
                tags=["subagent_complete"],
            )
            commit_sha = parent.append_turn(result_turn)

            final_sha = item["final_sha"]
            commit_msg = self.log_repo.get_commit_message(commit_sha)
            commit_tree = self.log_repo.get_tree(commit_sha)
            new_msg_lines = commit_msg.split("\n")
            new_msg_lines.append(f"Subagent-Result: {final_sha}")
            new_message = "\n".join(new_msg_lines)

            new_commit = self.log_repo.commit_tree(
                commit_tree, new_message, parent=self.log_repo.get_parent(commit_sha)
            )
            self.log_repo.update_ref(
                parent_branch, new_commit, self.log_repo.read_ref(parent_branch)
            )

    def trim(
        self,
        session_id: str | None = None,
        all_sessions: bool = False,
        keep_active: bool = True,
    ) -> int:
        prefix = "refs/agents/"
        if session_id and not all_sessions:
            prefix = f"refs/agents/{session_id}/"

        refs = self.log_repo.list_refs(prefix)
        active_branches = set()
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

    def retain_branches(self) -> None:
        pass

    def _get_depth(self, branch: str) -> int:
        parts = branch.split("/")
        agent_parts = [p for p in parts if p != "refs" and p != "agents"]
        return len(agent_parts)

    def is_active(self, subagent_id: str) -> bool:
        with self._lock:
            return subagent_id in self._active_subagents
