from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from gitlord.schemas import CommitTrailers, Turn


TRAILER_RE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):\s+(.+)$")
SUMMARY_RE = re.compile(
    r"^\[turn:(\d+)\]\[role:(\w+)\](?:\[tags:([^\]]*)\])?\s+(.*)$"
)
TURN_FILENAME_RE = re.compile(r"^(\d{20})-(\w+)\.json$")


class GitError(Exception):
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
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self._ensure_repo()

    def _ensure_repo(self) -> None:
        if (self.path / ".git").exists() or (self.path / "HEAD").exists():
            return
        self.path.mkdir(parents=True, exist_ok=True)
        _git("init", "--bare", repo=self.path)
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

    # ── Tree & Commit Construction ──────────────────────────────

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
        lines.append(f"Turn: {turn_number}")
        lines.append(f"Role: {role}")
        lines.append(f"Agent: {agent_id}")
        lines.append(f"Parent-Agent: {parent_agent_id or 'none'}")
        lines.append(f"Tool: {tool or 'none'}")
        lines.append(f"Tokens-In: {tokens_in}")
        lines.append(f"Tokens-Out: {tokens_out}")
        lines.append(f"Workspace-Commit: {workspace_commit or 'none'}")
        lines.append(f"Subagent-Result: {subagent_result or 'none'}")
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
        )
        return self.commit_tree(tree_sha, message, parent=parent_sha)

    def update_ref_cas(
        self, ref: str, new_sha: str, old_sha: str | None, rebuild_fn=None
    ) -> str:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if old_sha:
                    _git("update-ref", ref, new_sha, old_sha, repo=self.path)
                else:
                    _git("update-ref", ref, new_sha, repo=self.path)
                return new_sha
            except GitError as e:
                err = str(e)
                if (
                    "cannot lock ref" in err or "unexpected object" in err
                ) and attempt < max_retries - 1:
                    if rebuild_fn is None:
                        raise CASError(f"CAS update failed for {ref}: {err}") from e
                    old_sha = self.read_ref(ref)
                    new_sha = rebuild_fn(old_sha)
                    continue
                raise CASError(f"CAS update failed for {ref}: {err}") from e
        return new_sha  # unreachable

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

    # ── Updated existing methods ──────────────────────────────

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
        for i, line in enumerate(lines):
            m = SUMMARY_RE.match(line)
            if m:
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

        return CommitTrailers(
            turn=int(trailers.get("turn", 0)),
            role=trailers.get("role", ""),
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
