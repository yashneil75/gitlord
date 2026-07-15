from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from gitlord.schemas import CommitTrailers


TRAILER_RE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):\s+(.+)$")
SUMMARY_RE = re.compile(
    r"^\[turn:(\d+)\]\[role:(\w+)\](?:\[tags:([^\]]*)\])?\s+(.*)$"
)
TURN_FILENAME_RE = re.compile(r"^turns/(\d{20})-(\w+)\.json$")


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
        if not (self.path / ".git").exists():
            self.path.mkdir(parents=True, exist_ok=True)
            _git("init", "--bare", repo=self.path)

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
        input_text = "\n".join(lines) + "\n"
        result = subprocess.run(
            ["git", "mktree"],
            input=input_text,
            capture_output=True,
            text=True,
            cwd=str(self.path),
        )
        if result.returncode != 0:
            raise GitError(f"mktree failed: {result.stderr.strip()}")
        return result.stdout.rstrip("\n")

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

        old_tree = self.get_tree(parent_sha) if parent_sha else None

        if old_tree:
            entries = self.ls_tree(old_tree)
        else:
            entries = []

        new_entry = f"100644 blob {blob_sha}\t{turn_filename}"
        existing = [e for e in entries if e[3] != turn_filename]
        mktree_lines = [f"{m} {t} {s}\t{n}" for m, t, s, n in existing] + [new_entry]
        new_tree = self.mktree(mktree_lines)

        tag_str = ",".join(tags) if tags else ""
        summary_line = f"[turn:{turn_number}][role:{role}]"
        if tag_str:
            summary_line += f"[tags:{tag_str}]"
        summary_line += f" turn {turn_number}"

        msg_lines = [summary_line, ""]
        msg_lines.append(f"Turn: {turn_number}")
        msg_lines.append(f"Role: {role}")
        msg_lines.append(f"Agent: {trailers.agent_id}")
        msg_lines.append(
            f"Parent-Agent: {trailers.parent_agent_id or 'none'}"
        )
        msg_lines.append(f"Tool: {trailers.tool or 'none'}")
        msg_lines.append(f"Tokens-In: {trailers.tokens_in}")
        msg_lines.append(f"Tokens-Out: {trailers.tokens_out}")
        msg_lines.append(
            f"Workspace-Commit: {trailers.workspace_commit or 'none'}"
        )
        if trailers.subagent_result:
            msg_lines.append(
                f"Subagent-Result: {trailers.subagent_result}"
            )

        message = "\n".join(msg_lines)
        return self.commit_tree(new_tree, message, parent=parent_sha)

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
        for i, line in enumerate(lines):
            m = SUMMARY_RE.match(line)
            if m:
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

        tags_str = trailers.get("tags", "")
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str != "none" else []

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
            subagent_result=trailers.get("subagent-result"),
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
        tree = self.get_tree(sha)
        entries = self.ls_tree(tree)
        for _mode, _typ, _sha, name in entries:
            if TURN_FILENAME_RE.match(name):
                return name
        return None

    def get_turn_content(self, sha: str, turn_filename: str) -> str:
        return _git("show", f"{sha}:{turn_filename}", repo=self.path)

    def get_turn_content_by_sha(self, sha: str) -> str:
        return _git("show", sha, repo=self.path)
