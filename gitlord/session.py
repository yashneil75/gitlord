from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from typing import Any, Optional

import subprocess

from gitlord.schemas import SessionConfig, Turn, TurnRole
from gitlord.git import GitRepo, GitError
from gitlord.index import IndexBuilder
from gitlord.query import TurnQuery


def validate_session_id(session_id: str) -> None:
    if not session_id:
        raise ValueError("Session id must be non-empty")
    if session_id == "sub" or session_id.startswith("sub/"):
        raise ValueError(
            "Session id 'sub' is reserved for subagent branches "
            "(refs/agents/sub/...)"
        )
    result = subprocess.run(
        ["git", "check-ref-format", f"refs/agents/{session_id}"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise ValueError(
            f"Invalid session id {session_id!r}: not a valid git ref name "
            "(no spaces, '..', '~', '^', ':', '?', '*', '[', '\\\\', "
            "leading/trailing '/', trailing '.', or '.lock' suffix)"
        )


class Session:
    def __init__(
        self,
        log_repo: GitRepo,
        workspace_repo: GitRepo,
        config: SessionConfig,
        session_id: str,
    ) -> None:
        self.log_repo = log_repo
        self.workspace_repo = workspace_repo
        self.config = config
        self.session_id = session_id
        self._branch = f"refs/agents/{session_id}"

    @property
    def branch(self) -> str:
        return self._branch

    @classmethod
    def create(
        cls,
        session_id: str,
        config: SessionConfig,
    ) -> Session:
        validate_session_id(session_id)

        log_repo = GitRepo(config.log_repo_path, bare=True)
        workspace_repo = GitRepo(config.workspace_repo_path, bare=False)

        branch = f"refs/agents/{session_id}"
        if log_repo.ref_exists(branch):
            raise ValueError(f"Session {session_id} already exists")

        log_repo.create_orphan_branch(branch)

        session = cls(log_repo, workspace_repo, config, session_id)

        system_turn = Turn(
            turn=0,
            role=TurnRole.system,
            content=f"Session started at {datetime.now(timezone.utc).isoformat()}",
            agent_id=session_id,
            parent_agent_id=None,
        )
        session._commit_turn(system_turn)

        return session

    @classmethod
    def resume(
        cls,
        session_id: str,
        config: SessionConfig,
    ) -> Session:
        log_repo = GitRepo(config.log_repo_path, bare=True)
        workspace_repo = GitRepo(config.workspace_repo_path, bare=False)
        branch = f"refs/agents/{session_id}"
        if not log_repo.ref_exists(branch):
            raise ValueError(f"Session {session_id} not found")
        return cls(log_repo, workspace_repo, config, session_id)

    def _commit_turn(self, turn: Turn, subagent_result: str | None = None) -> str:
        turn.workspace_commit = self.workspace_repo.get_head()

        def rebuild(old_parent: str | None) -> str:
            turn.turn = self._next_turn_number()
            return self.log_repo.commit_turn(
                parent_sha=old_parent,
                turn=turn,
                agent_id=turn.agent_id,
                parent_agent_id=turn.parent_agent_id,
                subagent_result=subagent_result,
            )

        result_sha = self.log_repo.update_ref_cas(self.branch, new_sha, parent_sha, rebuild_fn=rebuild)

        try:
            from gitlord.index import IndexBuilder
            builder = IndexBuilder(self.log_repo)
            trailers = self.log_repo.parse_trailers(result_sha)
            if trailers:
                builder.append_turn(
                    session_id=self.session_id,
                    sha=result_sha,
                    trailers=trailers,
                )
        except Exception:
            pass  # index rebuild is best-effort

        return result_sha
        parent_sha = self.log_repo.read_ref(self.branch)
        new_sha = rebuild(parent_sha)
        result = self.log_repo.update_ref_cas(self.branch, new_sha, parent_sha, rebuild_fn=rebuild)

        if self.config.auto_index:
            self._rebuild_index()

        return result

    def _rebuild_index(self) -> None:
        try:
            builder = IndexBuilder(self.log_repo)
            index_data = builder.rebuild_json_index()

            index_path = Path(self.config.index_path)
            if not index_path.is_absolute():
                index_path = self.workspace_repo.path / index_path
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.write_text(json.dumps(index_data, indent=2, default=str))
        except Exception:
            pass

    def _next_turn_number(self) -> int:
        current = self.log_repo.read_ref(self.branch)
        if not current:
            return 0
        trailers = self.log_repo.parse_trailers(current)
        if trailers:
            return trailers.turn + 1
        return 0

    def snapshot(self, keep_recent: int = 5) -> None:
        commits = self.log_repo.log_branch(self.branch, format="%H", reverse=True)
        if len(commits) <= keep_recent + 1:
            return

        cutoff = len(commits) - keep_recent
        old_commits = commits[:cutoff]
        recent_commits = commits[cutoff:]

        snapshot_turns = []
        for sha in old_commits:
            turn = self.log_repo.get_turn_at_commit(sha)
            if turn:
                snapshot_turns.append(turn.model_dump(mode="json"))

        snap_dir = Path(self.workspace_repo.path) / ".gitlord"
        snap_dir.mkdir(exist_ok=True)
        snap_path = snap_dir / "snapshot.json"
        with open(snap_path, "w") as f:
            json.dump({
                "snapshot_from": old_commits[0],
                "snapshot_to": old_commits[-1],
                "turns": snapshot_turns,
            }, f, indent=2)

        snapshot_turn = Turn(
            turn=0,
            role=TurnRole.system,
            content=f"Snapshot of {len(old_commits)} turns ({old_commits[0][:8]}..{old_commits[-1][:8]})",
            agent_id=self.session_id,
        )

        empty_tree = self.log_repo.mktree([])
        snap_tree = self.log_repo.mktree([
            f"040000 tree {empty_tree}\tturns"
        ])
        snap_sha = self.log_repo.commit_tree(
            snap_tree,
            f"[turn:0][role:system] snapshot compressed",
            parent=None,
        )

        new_branch = f"{self.branch}-snapshot"
        self.log_repo.update_ref(new_branch, snap_sha)

        parent = snap_sha
        for sha in recent_commits:
            turn = self.log_repo.get_turn_at_commit(sha)
            if not turn:
                continue
            trailers = self.log_repo.parse_trailers(sha)
            if not trailers:
                continue
            new_sha = self.log_repo.commit_turn(
                parent_sha=parent,
                turn=turn,
                agent_id=turn.agent_id,
                parent_agent_id=turn.parent_agent_id,
            )
            parent = new_sha

        self.log_repo.update_ref(self.branch, parent)
        self.log_repo.delete_ref(new_branch)

    def append_turn(self, turn: Turn) -> str:
        turn.turn = self._next_turn_number()
        turn.timestamp = datetime.now(timezone.utc)
        if not turn.agent_id:
            turn.agent_id = self.session_id
        return self._commit_turn(turn)

    def append_user_turn(self, content: str, tags: list[str] | None = None) -> str:
        turn = Turn(
            turn=self._next_turn_number(),
            role=TurnRole.user,
            content=content,
            agent_id=self.session_id,
            tags=tags or [],
        )
        return self._commit_turn(turn)

    def append_assistant_turn(
        self,
        content: str,
        model: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        tags: list[str] | None = None,
        cost: float = 0.0,
    ) -> str:
        turn = Turn(
            turn=self._next_turn_number(),
            role=TurnRole.assistant,
            content=content,
            agent_id=self.session_id,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=cost,
            tags=tags or [],
        )
        return self._commit_turn(turn)

    def append_tool_call_turn(
        self,
        tool_name: str,
        tool_input: dict,
        tags: list[str] | None = None,
    ) -> str:
        turn = Turn(
            turn=self._next_turn_number(),
            role=TurnRole.tool_call,
            agent_id=self.session_id,
            tool_name=tool_name,
            tool_input=tool_input,
            tags=tags or [],
        )
        return self._commit_turn(turn)

    def append_tool_result_turn(
        self,
        tool_name: str,
        tool_output: str,
        tags: list[str] | None = None,
    ) -> str:
        turn = Turn(
            turn=self._next_turn_number(),
            role=TurnRole.tool_result,
            content=tool_output,
            agent_id=self.session_id,
            tool_name=tool_name,
            tool_output=tool_output,
            tags=tags or [],
        )
        return self._commit_turn(turn)

    def append_summary_turn(
        self,
        content: str,
        summarizes: list[str],
        model: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> str:
        turn = Turn(
            turn=self._next_turn_number(),
            role=TurnRole.summary,
            content=content,
            agent_id=self.session_id,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            summarizes=summarizes,
        )
        return self._commit_turn(turn)

    def rewind(self, target_sha: str, branch_name: str | None = None) -> Session:
        target_sha = self.log_repo.rev_parse(target_sha)
        if not self.log_repo.commit_exists(target_sha):
            raise ValueError(f"Commit {target_sha} not found")

        trailers = self.log_repo.parse_trailers(target_sha)
        if not trailers:
            raise ValueError(f"Commit {target_sha} has no valid trailers")

        commits = self.log_repo.log_branch(self.branch, format="%H", reverse=False)
        if target_sha not in commits:
            raise ValueError(f"Commit {target_sha} is not on branch {self.branch}")

        if branch_name:
            new_branch_name = branch_name
            if self.log_repo.ref_exists(new_branch_name):
                raise ValueError(f"Branch {new_branch_name} already exists")
        else:
            base = f"{self.branch}-rewind-{target_sha[:12]}"
            new_branch_name = base
            n = 2
            while self.log_repo.ref_exists(new_branch_name):
                new_branch_name = f"{base}-{n}"
                n += 1

        target_turn = self.log_repo.get_turn_at_commit(target_sha)
        if target_turn and target_turn.workspace_commit:
            try:
                self.workspace_repo.checkout(target_turn.workspace_commit)
            except GitError:
                pass

        self.log_repo.update_ref(new_branch_name, target_sha)

        new_config = self.config.model_copy()
        new_session = Session(
            log_repo=self.log_repo,
            workspace_repo=self.workspace_repo,
            config=new_config,
            session_id=self.session_id,
        )
        new_session._branch = new_branch_name
        return new_session

    def get_turns(self, start: int = 0, end: int | None = None) -> list[Turn]:
        commits = self.log_repo.log_branch(self.branch, format="%H", reverse=True)
        turns: list[Turn] = []
        for sha in commits:
            trailers = self.log_repo.parse_trailers(sha)
            if not trailers:
                continue
            if trailers.turn < start:
                continue
            if end is not None and trailers.turn > end:
                break
            turn = self.log_repo.get_turn_at_commit(sha)
            if turn:
                turns.append(turn)
        return turns

    def get_branch_path(self) -> str:
        return self._branch

    def get_turn_count(self) -> int:
        return self._next_turn_number()

    def query(self) -> TurnQuery:
        try:
            index_path = Path(self.config.index_path)
            if not index_path.is_absolute():
                index_path = self.workspace_repo.path / index_path
            if index_path.exists():
                return TurnQuery().load(str(index_path))
        except Exception:
            pass
        return TurnQuery()

    def snapshot(self, up_to_turn: int, output_path: str | None = None) -> int:
        from gitlord.snapshot import compress_to_snapshot
        snap_path = output_path or str(
            self.workspace_repo.path / ".gitlord" / f"snapshot-{self.session_id}.json"
        )
        count = compress_to_snapshot(
            self.log_repo, self._branch, up_to_turn, snap_path
        )
        return count

    def rebuild_index(self) -> None:
        self._rebuild_index()
