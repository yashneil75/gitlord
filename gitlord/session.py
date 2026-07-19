from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import subprocess

from gitlord.schemas import SessionConfig, Turn, TurnRole
from gitlord.git import GitRepo


def validate_session_id(session_id: str) -> None:
    """Reject session ids that would produce invalid or colliding refs."""
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
        log_repo = GitRepo(config.log_repo_path)
        workspace_repo = GitRepo(config.workspace_repo_path)

        branch = f"refs/agents/{session_id}"
        if log_repo.ref_exists(branch):
            raise ValueError(f"Session {session_id} already exists")  # caller bug

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
        log_repo = GitRepo(config.log_repo_path)
        workspace_repo = GitRepo(config.workspace_repo_path)
        branch = f"refs/agents/{session_id}"
        if not log_repo.ref_exists(branch):
            raise ValueError(f"Session {session_id} not found")  # caller bug
        return cls(log_repo, workspace_repo, config, session_id)

    def _commit_turn(self, turn: Turn, subagent_result: str | None = None) -> str:
        def build(parent_sha: str | None) -> str:
            # derive the turn number from the parent commit actually used,
            # so a CAS retry can never produce a stale/duplicate number
            trailers = (
                self.log_repo.parse_trailers(parent_sha) if parent_sha else None
            )
            turn.turn = trailers.turn + 1 if trailers else 0
            return self.log_repo.commit_turn(
                parent_sha=parent_sha,
                turn=turn,
                agent_id=turn.agent_id,
                parent_agent_id=turn.parent_agent_id,
                subagent_result=subagent_result,
            )

        parent_sha = self.log_repo.read_ref(self.branch)
        new_sha = build(parent_sha)
        return self.log_repo.update_ref_cas(self.branch, new_sha, parent_sha, rebuild_fn=build)

    def _next_turn_number(self) -> int:
        current = self.log_repo.read_ref(self.branch)
        if not current:
            return 0
        trailers = self.log_repo.parse_trailers(current)
        if trailers:
            return trailers.turn + 1
        return 0

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
    ) -> str:
        turn = Turn(
            turn=self._next_turn_number(),
            role=TurnRole.assistant,
            content=content,
            agent_id=self.session_id,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
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
            raise ValueError(f"Commit {target_sha} not found")  # caller bug

        trailers = self.log_repo.parse_trailers(target_sha)
        if not trailers:
            raise ValueError(f"Commit {target_sha} has no valid trailers")  # caller bug

        commits = self.log_repo.log_branch(self.branch, format="%H", reverse=False)
        if target_sha not in commits:
            raise ValueError(f"Commit {target_sha} is not on branch {self.branch}")  # caller bug

        if branch_name:
            new_branch_name = branch_name
            if self.log_repo.ref_exists(new_branch_name):
                raise ValueError(f"Branch {new_branch_name} already exists")  # caller bug
        else:
            # auto-generated names get a numeric suffix so rewinding to the
            # same commit repeatedly explores independent futures
            base = f"{self.branch}-rewind-{target_sha[:12]}"
            new_branch_name = base
            n = 2
            while self.log_repo.ref_exists(new_branch_name):
                new_branch_name = f"{base}-{n}"
                n += 1

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
