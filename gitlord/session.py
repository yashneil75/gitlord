from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from gitlord.schemas import CommitTrailers, SessionConfig, Turn, TurnRole
from gitlord.git import GitRepo


class Session:
    def __init__(
        self,
        log_repo: GitRepo,
        workspace_repo: GitRepo,
        config: SessionConfig,
        session_id: str,
    ):
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
        log_repo = GitRepo(config.log_repo_path)
        workspace_repo = GitRepo(config.workspace_repo_path)
        session = cls(log_repo, workspace_repo, config, session_id)

        if not log_repo.ref_exists(session.branch):
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
            raise ValueError(f"Session {session_id} not found")
        return cls(log_repo, workspace_repo, config, session_id)

    def _commit_turn(self, turn: Turn) -> str:
        turn_json = turn.model_dump_json(exclude_none=True)
        blob_sha = self.log_repo.hash_object(turn_json)

        parent_sha = self.log_repo.read_ref(self.branch)
        turn_number = turn.turn
        role = turn.role.value

        tags = turn.tags or []
        trailers = CommitTrailers(
            turn=turn_number,
            role=role,
            agent_id=turn.agent_id,
            parent_agent_id=turn.parent_agent_id,
            tool=turn.tool_name,
            tokens_in=turn.tokens_in,
            tokens_out=turn.tokens_out,
            workspace_commit=turn.workspace_commit,
            tags=tags,
        )

        def rebuild(new_parent: str) -> str:
            return self.log_repo.commit_tree_from_turns(
                parent_sha=new_parent,
                turn_number=turn_number,
                role=role,
                blob_sha=blob_sha,
                trailers=trailers,
                tags=tags,
            )

        commit_sha = self.log_repo.commit_tree_from_turns(
            parent_sha=parent_sha,
            turn_number=turn_number,
            role=role,
            blob_sha=blob_sha,
            trailers=trailers,
            tags=tags,
        )

        return self.log_repo.update_ref_cas(self.branch, commit_sha, parent_sha, rebuild_fn=rebuild)

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
        if not self.log_repo.commit_exists(target_sha):
            raise ValueError(f"Commit {target_sha} not found")

        trailers = self.log_repo.parse_trailers(target_sha)
        if not trailers:
            raise ValueError(f"Commit {target_sha} has no valid trailers")

        new_branch_name = branch_name or f"{self.branch}-rewind-{target_sha[:12]}"

        if self.log_repo.ref_exists(new_branch_name):
            raise ValueError(f"Branch {new_branch_name} already exists")

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
        turns = []
        for sha in commits:
            trailers = self.log_repo.parse_trailers(sha)
            if trailers and trailers.turn >= start:
                if end is not None and trailers.turn > end:
                    break
                turn_content = self._read_turn_from_commit(sha)
                if turn_content:
                    turns.append(turn_content)
        return turns

    def _read_turn_from_commit(self, sha: str) -> Optional[Turn]:
        turn_filename = self.log_repo.get_turn_filename(sha)
        if not turn_filename:
            return None
        content = self.log_repo.get_turn_content(sha, turn_filename)
        data = json.loads(content)
        return Turn(**data)

    def get_branch_path(self) -> str:
        return self._branch

    def get_turn_count(self) -> int:
        return self._next_turn_number()
