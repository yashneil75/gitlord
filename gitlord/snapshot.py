from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from gitlord.git import GitRepo
from gitlord.schemas import Turn, TurnRole
from gitlord.index import IndexBuilder


def compress_to_snapshot(
    log_repo: GitRepo,
    branch: str,
    up_to_turn: int,
    output_path: str | Path,
) -> int:
    commits = log_repo.log_branch(branch, format="%H", reverse=True)
    target_sha = None
    last_sha = None
    snapshot_turns: list[dict[str, Any]] = []

    for sha in commits:
        trailers = log_repo.parse_trailers(sha)
        if not trailers:
            continue
        if trailers.turn <= up_to_turn:
            if target_sha is None:
                target_sha = sha
            turn_data = _read_turn_json(log_repo, sha)
            if turn_data:
                snapshot_turns.append(turn_data)
            last_sha = sha
        else:
            break

    if target_sha is None:
        return 0

    snapshot = {
        "version": 1,
        "snapshot_of": branch,
        "up_to_turn": up_to_turn,
        "up_to_sha": target_sha,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "turns": snapshot_turns,
        "total_turns": len(snapshot_turns),
    }

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, indent=2, default=str))

    return len(snapshot_turns)


def rebase_from_snapshot(
    log_repo: GitRepo,
    branch: str,
    snapshot_path: str | Path,
    new_branch: str | None = None,
) -> Optional[str]:
    snapshot_path = Path(snapshot_path)
    if not snapshot_path.exists():
        return None

    snapshot = json.loads(snapshot_path.read_text())
    up_to_sha = snapshot.get("up_to_sha")
    if not up_to_sha:
        return None

    target_branch = new_branch or branch

    if log_repo.ref_exists(target_branch):
        return None

    parent_sha = log_repo.read_ref(branch)
    if not parent_sha:
        return None

    commits = log_repo.log_branch(branch, format="%H", reverse=True)
    post_snapshot_commits: list[str] = []
    found = False
    for sha in commits:
        if sha == up_to_sha:
            found = True
        elif found:
            post_snapshot_commits.append(sha)

    if not post_snapshot_commits and snapshot_path != Path("snapshot.json"):
        return None

    log_repo.update_ref(target_branch, post_snapshot_commits[0] if post_snapshot_commits else up_to_sha)

    for sha in post_snapshot_commits[1:]:
        trailers = log_repo.parse_trailers(sha)
        if not trailers:
            continue
        try:
            parent = log_repo.get_parent(sha)
            log_repo.update_ref(target_branch, sha, old_sha=parent)
        except Exception:
            pass

    return target_branch


def _read_turn_json(repo: GitRepo, sha: str) -> Optional[dict[str, Any]]:
    turn_filename = repo.get_turn_filename(sha)
    if not turn_filename:
        return None
    try:
        raw = repo.get_turn_content(sha, turn_filename)
        return json.loads(raw)
    except (json.JSONDecodeError, Exception):
        return None
