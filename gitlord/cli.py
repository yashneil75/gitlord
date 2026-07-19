from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

try:
    from typer import Typer, Argument, Option, run
    HAS_TYPER = True
except ImportError:
    HAS_TYPER = False

from gitlord.config import load_config
from gitlord.git import GitRepo
from gitlord.index import IndexBuilder
from gitlord.mcp import MCPMon, MCPServerConfig
from gitlord.rag import VectorIndex
from gitlord.session import Session
from gitlord.subagent import SubagentManager

if HAS_TYPER:
    app = Typer(name="gitlord", help="Agent orchestration framework with git-backed storage")
else:
    app = None


def _get_session(
    session_id: str,
    config_path: Optional[str] = None,
    create: bool = False,
) -> Session:
    config = load_config(config_path)
    if create:
        return Session.create(session_id, config.session)
    return Session.resume(session_id, config.session)


if HAS_TYPER:

    @app.command()
    def run(
        prompt: str = Argument(..., help="Prompt to send to the agent"),
        session: str = Option(None, "--session", help="Session ID"),
        model: str = Option(None, "--model", help="Model name override"),
        config: str = Option(None, "--config", help="Path to config file"),
    ) -> None:
        """Start or continue a session"""
        cfg = load_config(config)
        if model:
            cfg.session.agent.model = model

        session_id = session
        if not session_id:
            from gitlord.subagent import _new_ulid
            session_id = _new_ulid()

        # "start or continue": resume the session if it already exists
        log_repo = GitRepo(cfg.session.log_repo_path)
        exists = log_repo.ref_exists(f"refs/agents/{session_id}")
        sess = _get_session(session_id, config, create=not exists)
        sha = sess.append_user_turn(prompt)
        print(f"Session: {session_id}")
        print(f"Turn committed: {sha}")

    @app.command()
    def log(
        session_id: str = Argument(..., help="Session ID"),
        branch: str = Option(None, "--branch", help="Branch path (default: root)"),
    ) -> None:
        """Show human-readable turn history"""
        sess = _get_session(session_id)
        branch_path = f"refs/agents/{session_id}"
        if branch:
            branch_path = f"refs/agents/{branch}"

        commits = sess.log_repo.log_branch(branch_path, format="%H", reverse=True)
        for sha in commits:
            trailers = sess.log_repo.parse_trailers(sha)
            if not trailers:
                continue
            tag_str = f" [{','.join(trailers.tags)}]" if trailers.tags else ""
            print(f"{sha[:12]}  turn:{trailers.turn}  role:{trailers.role}{tag_str}")

    @app.command()
    def tree(
        session_id: str = Argument(..., help="Session ID"),
    ) -> None:
        """Render agent/subagent branch structure"""
        sess = _get_session(session_id)
        refs = list(sess.log_repo.list_refs(f"refs/agents/{session_id}"))
        sub_refs = list(sess.log_repo.list_refs(f"refs/agents/sub/{session_id}/"))
        for ref in sorted(set(refs + sub_refs)):
            if ref.startswith(f"refs/agents/sub/{session_id}/"):
                depth = ref.count("/") - 4
                display = ref.split("/")[-1]
            else:
                depth = 0
                display = ref.split("/")[-1]
            indent = "  " * depth
            sha = sess.log_repo.read_ref(ref) or "?"
            print(f"{indent}{display} @ {sha[:12]}")

    @app.command()
    def show(
        sha: str = Argument(..., help="Commit SHA"),
        session: str = Option(None, "--session", help="Session ID (for repo location)"),
        config: str = Option(None, "--config", help="Path to config file"),
    ) -> None:
        """Print full turn JSON at commit"""
        cfg = load_config(config)
        repo = GitRepo(cfg.session.log_repo_path)
        turn_filename = repo.get_turn_filename(sha)
        if not turn_filename:
            print(f"No turn file found at commit {sha}", file=sys.stderr)
            sys.exit(1)
        content = repo.get_turn_content(sha, turn_filename)
        print(content)

    @app.command()
    def rewind(
        session_id: str = Argument(..., help="Session ID"),
        to: str = Option(..., "--to", help="Target commit SHA"),
        run: str = Option(None, "--run", help="Prompt to re-run from"),
        branch_name: str = Option(None, "--branch-name", help="New branch name"),
    ) -> None:
        """Checkout branch at sha, optionally re-run from there"""
        sess = _get_session(session_id)
        new_session = sess.rewind(to, branch_name)
        print(f"New branch: {new_session.get_branch_path()}")

        if run:
            sha = new_session.append_user_turn(run)
            print(f"Turn committed: {sha}")

    @app.command()
    def diff(
        sha1: str = Argument(..., help="First commit SHA"),
        sha2: str = Argument(..., help="Second commit SHA"),
        session: str = Option(None, "--session", help="Session ID"),
        config: str = Option(None, "--config", help="Path to config file"),
    ) -> None:
        """Diff turn content between two commits"""
        cfg = load_config(config)
        repo = GitRepo(cfg.session.log_repo_path)

        def get_turn_content(sha: str) -> str:
            tf = repo.get_turn_filename(sha)
            if not tf:
                return ""
            return repo.get_turn_content(sha, tf)

        content1 = get_turn_content(sha1)
        content2 = get_turn_content(sha2)

        if content1 == content2:
            print("No differences")
            return

        try:
            import difflib
            lines1 = content1.splitlines(keepends=True)
            lines2 = content2.splitlines(keepends=True)
            for line in difflib.unified_diff(
                lines1, lines2,
                fromfile=sha1[:12], tofile=sha2[:12],
            ):
                sys.stdout.write(line)
        except ImportError:
            print(f"--- {sha1[:12]}")
            print(f"+++ {sha2[:12]}")
            print(content1)
            print("---")
            print(content2)

    @app.command()
    def index(
        rebuild: bool = Option(False, "--rebuild", help="Rebuild JSON and vector indexes"),
        config: str = Option(None, "--config", help="Path to config file"),
    ) -> None:
        """Rebuild indexes from git log"""
        cfg = load_config(config)
        repo = GitRepo(cfg.session.log_repo_path)
        vi = None
        if cfg.session.agent.rag_enabled:
            try:
                vi = VectorIndex()
            except Exception as e:
                print(f"Vector index unavailable: {e}", file=sys.stderr)

        builder = IndexBuilder(repo, vi)
        index_data = builder.to_file()
        print(f"Index rebuilt: {len(index_data.get('sessions', {}))} sessions")

        if vi:
            chunks = builder.rebuild_vector_index()
            print(f"Vector index rebuilt: {chunks} chunks indexed")

    @app.command()
    def mcp_add(
        name: str = Argument(..., help="Server name"),
        command: str = Argument(..., help="Server command"),
        args: list[str] = Argument(None, help="Command arguments"),
        config: str = Option(None, "--config", help="Path to config file"),
    ) -> None:
        """Append MCPServerConfig to config"""
        cfg = load_config(config)
        server = MCPServerConfig(name=name, command=command, args=args or [])
        cfg.session.mcp_servers.append(server)

        config_path = Path(config or "gitlord.json")
        data = {"session": cfg.session.model_dump()}
        config_path.write_text(json.dumps(data, indent=2, default=str))
        print(f"Added MCP server '{name}' to {config_path}")

    @app.command()
    def trim(
        session_id: str = Option(None, "--session", help="Session ID to trim"),
        all: bool = Option(False, "--all", help="Trim all completed subagent branches"),
        config: str = Option(None, "--config", help="Path to config file"),
    ) -> None:
        """Delete completed subagent branches"""
        cfg = load_config(config)
        repo = GitRepo(cfg.session.log_repo_path)
        mgr = SubagentManager(
            log_repo=repo,
            workspace_repo=repo,
            config=cfg.session,
            session_id=session_id or "",
        )
        count = mgr.trim(session_id=session_id, all_sessions=all)
        print(f"Trimmed {count} branches")


def main() -> None:
    if not HAS_TYPER:
        print("Error: typer is required for CLI. Install with: pip install typer", file=sys.stderr)
        sys.exit(1)
    app()


if __name__ == "__main__":
    main()
