from __future__ import annotations

import json
import random
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from gitlord.schemas import CommitTrailers, Turn, GitlordError


TRAILER_RE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):\s+(.+)$")
SUMMARY_RE = re.compile(
    r"^\[turn:(\d+)\]\[role:(\w+)\](?:\[tags:([^\]]*)\])?\s+(.*)$"
)
TURN_FILENAME_RE = re.compile(r"^(\d{20})-(\w+)\.json$")

TRAILER_NAME_MAP = {
    "turn-id": "turn_id",
    "turn-tokens": "turn_tokens",
    "turn-cost": "turn_cost",
    "turn-error": "turn_error",
    "tool-calls": "tool_calls",
    "subagent-id": "subagent_id",
    "parent-sha": "parent_sha",
    "turn": "turn",
    "role": "role",
    "agent": "agent_id",
    "parent-agent": "parent_agent_id",
    "tool": "tool",
    "tokens-in": "tokens_in",
    "tokens-out": "tokens_out",
    "cost": "cost",
    "error": "error",
    "workspace-commit": "workspace_commit",
    "subagent-result": "subagent_result",
}


class GitError(GitlordError):
    pass


class CASError(GitError):
    pass


def _git(*args: str, repo: str | Path, log_stderr: bool = False) -> str:
    cmd = ["git"] + list(args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(repo),
    )
    if result.returncode != 0:
        if log_stderr:
            raise GitError(result.stderr.strip())
        raise GitError(
            f"git {' '.join(args)} failed: {result.stderr.strip()}"
        )
    return result.stdout.rstrip("\n")


class GitRepo:
    def __init__(self, path: str | Path, bare: bool = True) -> None:
        self.path = Path(path).resolve()
        self._bare = bare
        self._ensure_repo()

    def _ensure_repo(self) -> None:
        if (self.path / ".git").exists() or (self.path / "HEAD").exists():
            return
        self.path.mkdir(parents=True, exist_ok=True)
        if self._bare:
            _git("init", "--bare", repo=self.path)
        else:
            _git("init", repo=self.path)
        _git("config", "user.name", "GitLord Agent", repo=self.path)
        _git("config", "user.email", "agent@gitlord.local", repo=self.path)

    def hash_object(self, content: str | bytes) -> str:
        if isinstance(content, str):
            content = content.encode("utf-8")
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(content)
            tmp = f.name
        try:
            return _git("hash-object", "-w", tmp, repo=self.path)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def mktree(self, lines: list[str]) -> str:
        if not lines:
            input_data = b""
        else:
            input_data = ("\n".join(lines) + "\n").encode("utf-8")
        result = subprocess.run(
            ["git", "mktree"],
            input=input_data,
            capture_output=True,
            cwd=str(self.path),
        )
        if result.returncode != 0:
            raise GitError(f"mktree failed: {result.stderr.strip()}")
        return result.stdout.decode("utf-8").rstrip("\n")

    def commit_tree(
        self,
        tree: str,
        message: str,
        parent: Optional[str] = None,
    ) -> str:
        cmd = ["commit-tree", tree, "-m", message]
        if parent:
            cmd.extend(["-p", parent])
        return _git(*cmd, repo=self.path)

    def update_ref(
        self,
        ref: str,
        new_sha: str,
        old_sha: Optional[str] = None,
    ) -> None:
        try:
            if old_sha:
                _git("update-ref", ref, new_sha, old_sha, repo=self.path)
            else:
                _git("update-ref", ref, new_sha, repo=self.path)
        except GitError as e:
            raise CASError(str(e)) from e

    def read_ref(self, ref: str) -> Optional[str]:
        try:
            return _git("show-ref", "--verify", "--hash", ref, repo=self.path)
        except GitError:
            return None

    def delete_ref(self, ref: str) -> None:
        try:
            _git("update-ref", "-d", ref, repo=self.path)
        except GitError:
            pass

    def ref_exists(self, ref: str) -> bool:
        return self.read_ref(ref) is not None

    def list_refs(self, prefix: str = "refs/agents/") -> list[str]:
        try:
            output = _git("for-each-ref", "--format=%(refname)", prefix, repo=self.path)
            if not output:
                return []
            return output.split("\n")
        except GitError:
            return []

    def get_commit_message(self, sha: str) -> str:
        return _git("log", "--format=%B", "-n", "1", sha, repo=self.path)

    def get_parent(self, sha: str) -> Optional[str]:
        try:
            return _git("rev-parse", f"{sha}^", repo=self.path)
        except GitError:
            return None

    def get_head(self) -> Optional[str]:
        try:
            sha = _git("rev-parse", "HEAD", repo=self.path)
            if not sha or sha == "HEAD":
                return None
            return sha
        except GitError:
            return None

    def checkout(self, sha: str) -> None:
        _git("checkout", "-f", sha, repo=self.path)

    def get_tree(self, commit_sha: str) -> str:
        return _git("rev-parse", f"{commit_sha}:", repo=self.path)

    def ls_tree(self, tree_sha: str) -> list[tuple[str, str, str, str]]:
        output = _git("ls-tree", tree_sha, repo=self.path)
        if not output:
            return []
        entries = []
        for line in output.split("\n"):
            if not line.strip():
                continue
            parts = line.split(None, 3)
            if len(parts) == 4:
                mode, typ, sha, name = parts
                entries.append((mode, typ, sha, name))
        return entries

    def show_object(self, sha: str) -> str:
        return _git("show", sha, repo=self.path)

    def commit_exists(self, sha: str) -> bool:
        try:
            _git("cat-file", "-e", sha, repo=self.path)
            return True
        except GitError:
            return False

    def rev_parse(self, ref: str) -> str:
        return _git("rev-parse", ref, repo=self.path)

    def log_branch(
        self,
        ref: str,
        format: str = "%H",
        reverse: bool = False,
    ) -> list[str]:
        order = "--reverse" if reverse else "--topo-order"
        try:
            output = _git(
                "log",
                order,
                f"--format={format}",
                ref,
                repo=self.path,
            )
            if not output:
                return []
            return output.split("\n")
        except GitError:
            return []

    def _build_tree_with_turn(
        self, parent_sha: str | None, turn_filename: str, blob_sha: str
    ) -> str:
        turn_short = turn_filename.removeprefix("turns/")
        turn_entry = f"100644 blob {blob_sha}\t{turn_short}"

        if parent_sha is None:
            turns_tree_sha = self.mktree([turn_entry])
            return self.mktree([f"040000 tree {turns_tree_sha}\tturns"])

        parent_tree = self.get_tree(parent_sha)
        entries = self.ls_tree(parent_tree)

        turns_sha = None
        other_root = []
        for mode, typ, sha_val, name in entries:
            if name == "turns":
                turns_sha = sha_val
            else:
                other_root.append(f"{mode} {typ} {sha_val}\t{name}")

        if turns_sha is None:
            turns_tree_sha = self.mktree([turn_entry])
        else:
            turn_entries = self.ls_tree(turns_sha)
            filtered = [
                (m, t, s, n)
                for m, t, s, n in turn_entries
                if n != turn_short
            ]
            filtered.append(("100644", "blob", blob_sha, turn_short))
            filtered.sort(key=lambda x: x[3])
            lines = [f"{m} {t} {s}\t{n}" for m, t, s, n in filtered]
            turns_tree_sha = self.mktree(lines)

        root_lines = [f"040000 tree {turns_tree_sha}\tturns"] + other_root
        return self.mktree(root_lines)

    @staticmethod
    def _build_commit_message(
        turn_number: int,
        role: str,
        tags: list[str],
        agent_id: str,
        parent_agent_id: str | None,
        tool: str | None,
        tokens_in: int,
        tokens_out: int,
        workspace_commit: str | None,
        subagent_result: str | None,
        turn_id: str | None = None,
        tokens: int = 0,
        cost: float = 0.0,
        error: str | None = None,
        tool_calls: list[dict] | None = None,
        turn_id: str = "",
        cost: float = 0.0,
        error: str | None = None,
        turn_tokens: int = 0,
        subagent_id: str | None = None,
        parent_sha: str | None = None,
    ) -> str:
        tag_str = ",".join(tags) if tags else ""
        summary = f"turn {turn_number} by {role}"
        if len(summary) > 72:
            summary = summary[:69] + "..."

        header = f"[turn:{turn_number}][role:{role}]"
        if tag_str:
            header += f"[tags:{tag_str}]"
        header += f" {summary}"

        lines = [header, ""]
        turn_id_val = turn_id or f"t{turn_number}"
        lines.append(f"Turn-ID: {turn_id_val}")
        lines.append(f"Role: {role}")
        lines.append(f"Agent: {agent_id}")
        lines.append(f"Parent-Agent: {parent_agent_id or 'none'}")
        lines.append(f"Tool: {tool or 'none'}")
        turn_tokens_val = turn_tokens if turn_tokens > 0 else tokens_in + tokens_out
        lines.append(f"Turn-Tokens: {turn_tokens_val}")
        lines.append(f"Turn-Cost: {cost:.6f}")
        lines.append(f"Turn-Error: {error or 'none'}")
        lines.append(f"Tokens-In: {tokens_in}")
        lines.append(f"Tokens-Out: {tokens_out}")
        lines.append(f"Cost: {cost:.6f}")
        lines.append(f"Error: {error or 'none'}")
        lines.append(f"Workspace-Commit: {workspace_commit or 'none'}")
        lines.append(f"Subagent-Result: {subagent_result or 'none'}")
        lines.append(f"Turn-ID: {turn_id or 'none'}")
        lines.append(f"Turn-Tokens: {tokens}")
        lines.append(f"Turn-Cost: {cost}")
        lines.append(f"Turn-Error: {error or 'none'}")
        lines.append(f"Tool-Calls: {json.dumps(tool_calls) if tool_calls else 'none'}")
        lines.append(f"Subagent-ID: {subagent_id or 'none'}")
        lines.append(f"Parent-SHA: {parent_sha or 'none'}")
        return "\n".join(lines)

    def commit_turn(
        self,
        parent_sha: str | None,
        turn: Turn,
        agent_id: str,
        parent_agent_id: str | None,
        subagent_result: str | None = None,
    ) -> str:
        content = turn.model_dump_json()
        blob_sha = self.hash_object(content)
        turn_filename = f"turns/{turn.turn:020d}-{turn.role.value}.json"
        tree_sha = self._build_tree_with_turn(parent_sha, turn_filename, blob_sha)
        message = self._build_commit_message(
            turn_number=turn.turn,
            role=turn.role.value,
            tags=turn.tags,
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
            tool=turn.tool_name,
            tokens_in=turn.tokens_in,
            tokens_out=turn.tokens_out,
            workspace_commit=turn.workspace_commit,
            subagent_result=subagent_result,
            turn_id=turn.turn_id,
            tokens=turn.tokens,
            cost=turn.cost,
            error=turn.error,
            tool_calls=turn.tool_calls,
            subagent_id=turn.subagent_id,
            parent_sha=turn.parent_sha,
            cost=turn.cost,
            error=turn.error,
            turn_tokens=turn.tokens_in + turn.tokens_out,
        )
        return self.commit_tree(tree_sha, message, parent=parent_sha)

    def update_ref_cas(
        self,
        ref: str,
        new_sha: str,
        old_sha: str | None,
        rebuild_fn=None,
        max_retries: int = 64,
    ) -> str:
        for attempt in range(max_retries):
            try:
                expected = old_sha if old_sha else "0" * 40
                _git("update-ref", ref, new_sha, expected, repo=self.path)
                return new_sha
            except GitError as e:
                err = str(e)
                retryable = (
                    "cannot lock ref" in err
                    or "unexpected object" in err
                    or "but expected" in err
                    or "File exists" in err
                )
                if retryable and attempt < max_retries - 1:
                    if rebuild_fn is None:
                        raise CASError(f"CAS update failed for {ref}: {err}") from e
                    delay = min(0.002 * (2 ** min(attempt, 6)), 0.2)
                    time.sleep(delay * (0.5 + random.random()))
                    old_sha = self.read_ref(ref)
                    new_sha = rebuild_fn(old_sha)
                    continue
                raise CASError(f"CAS update failed for {ref}: {err}") from e
        return new_sha

    def create_orphan_branch(self, ref: str) -> str:
        empty_tree = self.mktree([])
        root_tree = self.mktree([f"040000 tree {empty_tree}\tturns"])
        commit_sha = self.commit_tree(root_tree, "root commit — session start", parent=None)
        self.update_ref(ref, commit_sha)
        return commit_sha

    def get_turn_at_commit(self, sha: str) -> Turn | None:
        try:
            tree_sha = self.get_tree(sha)
            entries = self.ls_tree(tree_sha)
            turns_sha = None
            for _mode, _typ, sha_val, name in entries:
                if name == "turns":
                    turns_sha = sha_val
                    break
            if turns_sha is None:
                return None
            turn_entries = self.ls_tree(turns_sha)
            if not turn_entries:
                return None
            _mode, _typ, turn_blob_sha, _name = turn_entries[-1]
            content = _git("show", turn_blob_sha, repo=self.path)
            return Turn.model_validate_json(content)
        except GitError:
            return None

    def get_turn_content_raw(self, sha: str, turn_number: int) -> str | None:
        try:
            tree_sha = self.get_tree(sha)
            entries = self.ls_tree(tree_sha)
            turns_sha = None
            for _mode, _typ, sha_val, name in entries:
                if name == "turns":
                    turns_sha = sha_val
                    break
            if turns_sha is None:
                return None
            turn_entries = self.ls_tree(turns_sha)
            prefix = f"{turn_number:020d}-"
            for _mode, _typ, blob_sha, filename in turn_entries:
                if filename.startswith(prefix):
                    return _git("show", blob_sha, repo=self.path)
            return None
        except GitError:
            return None

    def commit_tree_from_turns(
        self,
        parent_sha: Optional[str],
        turn_number: int,
        role: str,
        blob_sha: str,
        trailers: CommitTrailers,
        tags: list[str],
    ) -> str:
        turn_filename = f"turns/{turn_number:020d}-{role}.json"
        tree_sha = self._build_tree_with_turn(parent_sha, turn_filename, blob_sha)
        message = self._build_commit_message(
            turn_number=turn_number,
            role=role,
            tags=tags,
            agent_id=trailers.agent_id,
            parent_agent_id=trailers.parent_agent_id,
            tool=trailers.tool,
            tokens_in=trailers.tokens_in,
            tokens_out=trailers.tokens_out,
            workspace_commit=trailers.workspace_commit,
            subagent_result=trailers.subagent_result,
            turn_id=trailers.turn_id,
            tokens=trailers.tokens,
            cost=trailers.cost,
            error=trailers.error,
            tool_calls=trailers.tool_calls,
            cost=trailers.cost,
            error=trailers.error,
            turn_tokens=trailers.turn_tokens,
            subagent_id=trailers.subagent_id,
            parent_sha=trailers.parent_sha,
        )
        return self.commit_tree(tree_sha, message, parent=parent_sha)

    def parse_trailers(self, sha: str) -> Optional[CommitTrailers]:
        try:
            msg = self.get_commit_message(sha)
        except GitError:
            return None
        return self._parse_commit_message(msg)

    @staticmethod
    def _parse_commit_message(msg: str) -> Optional[CommitTrailers]:
        lines = msg.split("\n")
        body_start = 0
        tags: list[str] = []
        role = ""
        turn_num = 0
        for i, line in enumerate(lines):
            m = SUMMARY_RE.match(line)
            if m:
                turn_num = int(m.group(1))
                role = m.group(2)
                tag_str = m.group(3) or ""
                tags = [t.strip() for t in tag_str.split(",") if t.strip()] if tag_str else []
                body_start = i + 2
                break

        trailers: dict[str, str] = {}
        for line in lines[body_start:]:
            line = line.strip()
            if not line:
                continue
            tm = TRAILER_RE.match(line)
            if tm:
                trailers[tm.group(1).lower()] = tm.group(2)

        if not trailers:
            return None

        turn_id = trailers.get("turn-id", f"t{turn_num}")

        tool_calls_raw = trailers.get("tool-calls", "none")
        tool_calls = None
        if tool_calls_raw != "none":
            try:
                tool_calls = json.loads(tool_calls_raw)
            except (json.JSONDecodeError, Exception):
                pass

        turn_tokens_str = trailers.get("turn-tokens", "0")
        try:
            turn_tokens = int(turn_tokens_str)
        except (ValueError, TypeError):
            turn_tokens = 0

        if turn_tokens == 0:
            tokens_in = int(trailers.get("tokens-in", 0))
            tokens_out = int(trailers.get("tokens-out", 0))
            turn_tokens = tokens_in + tokens_out

        cost_str = trailers.get("turn-cost", trailers.get("cost", "0"))
        try:
            cost = float(cost_str)
        except (ValueError, TypeError):
            cost = 0.0

        error = trailers.get("turn-error", trailers.get("error", "none"))
        if error == "none":
            error = None

        return CommitTrailers(
            turn=turn_num,
            turn_id=turn_id,
            role=role or trailers.get("role", ""),
            agent_id=trailers.get("agent", ""),
            parent_agent_id=(
                trailers.get("parent-agent")
                if trailers.get("parent-agent") != "none"
                else None
            ),
            tool=(
                trailers.get("tool")
                if trailers.get("tool") != "none"
                else None
            ),
            tokens_in=int(trailers.get("tokens-in", 0)),
            tokens_out=int(trailers.get("tokens-out", 0)),
            cost=cost,
            error=error,
            workspace_commit=(
                trailers.get("workspace-commit")
                if trailers.get("workspace-commit") != "none"
                else None
            ),
            subagent_result=(
                trailers.get("subagent-result")
                if trailers.get("subagent-result") is not None and trailers.get("subagent-result") != "none"
                else None
            ),
            tags=tags,
            turn_id=(
                trailers.get("turn-id")
                if trailers.get("turn-id") != "none"
                else None
            ),
            tokens=int(trailers.get("turn-tokens", 0)),
            cost=float(trailers.get("turn-cost", 0.0)),
            error=(
                trailers.get("turn-error")
                if trailers.get("turn-error") != "none"
                else None
            ),
            tool_calls=(
                json.loads(trailers["tool-calls"])
                if trailers.get("tool-calls") and trailers.get("tool-calls") != "none"
            tool_calls=tool_calls,
            turn_tokens=turn_tokens,
            parent_sha=(
                trailers.get("parent-sha")
                if trailers.get("parent-sha") != "none"
                else None
            ),
            subagent_id=(
                trailers.get("subagent-id")
                if trailers.get("subagent-id") != "none"
                else None
            ),
            parent_sha=(
                trailers.get("parent-sha")
                if trailers.get("parent-sha") != "none"
                else None
            ),
        )

    def get_turn_number_from_branch(self, ref: str) -> int:
        commits = self.log_branch(ref, format="%H", reverse=True)
        if not commits:
            return 0
        last = commits[-1]
        trailers = self.parse_trailers(last)
        if trailers:
            return trailers.turn + 1
        return 0

    def get_turn_filename(self, sha: str) -> Optional[str]:
        tree_sha = self.get_tree(sha)
        entries = self.ls_tree(tree_sha)
        turns_sha = None
        for _mode, _typ, sha_val, name in entries:
            if name == "turns":
                turns_sha = sha_val
                break
        if turns_sha is None:
            return None
        turn_entries = self.ls_tree(turns_sha)
        last_name: str | None = None
        for _mode, _typ, _blob_sha, name in turn_entries:
            if TURN_FILENAME_RE.match(name):
                last_name = name
        return f"turns/{last_name}" if last_name else None

    def get_turn_content(self, sha: str, turn_filename: str) -> str:
        return _git("show", f"{sha}:{turn_filename}", repo=self.path)

    def get_turn_content_by_sha(self, sha: str) -> str:
        return _git("show", sha, repo=self.path)
