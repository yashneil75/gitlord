import json
import os
from pathlib import Path
from gitlord.git import GitRepo
from gitlord.index import IndexBuilder
from gitlord.schemas import Turn, TurnRole


def test_append_turn_adds_to_index(tmp_path):
    repo = GitRepo(tmp_path / "log")
    builder = IndexBuilder(repo)

    root = repo.create_orphan_branch("refs/agents/sess1")
    turn = Turn(
        turn=1, role=TurnRole.user, content="hi", agent_id="sess1",
    )
    sha = repo.commit_turn(root, turn, "sess1", None)
    trailers = repo.parse_trailers(sha)

    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        builder.append_turn("sess1", sha, trailers)
        with open(tmp_path / ".gitlord" / "index.json") as f:
            index = json.load(f)
        assert "sess1" in index["sessions"]
        assert len(index["sessions"]["sess1"]["turns"]) == 1
        assert index["sessions"]["sess1"]["turns"][0]["sha"] == sha
    finally:
        os.chdir(old_cwd)
