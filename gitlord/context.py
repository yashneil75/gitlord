from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from gitlord.schemas import Turn, TurnRole, SessionConfig
from gitlord.git import GitRepo


@dataclass
class DedupIndex:
    _map: dict[tuple[str, str], tuple[int, str]] = field(default_factory=dict)

    def get(self, branch: str, path: str) -> tuple[int, str] | None:
        return self._map.get((branch, path))

    def set(self, branch: str, path: str, turn: int, content_hash: str) -> None:
        self._map[(branch, path)] = (turn, content_hash)

    def invalidate(self, branch: str, path: str) -> None:
        self._map.pop((branch, path), None)

    def rebuild_from_log(
        self,
        repo: GitRepo,
        branch: str,
        max_commits: int = 1000,
    ) -> None:
        commits = repo.log_branch(branch, format="%H %s", reverse=True)
        for entry in commits[-max_commits:]:
            sha = entry.split()[0]
            trailers = repo.parse_trailers(sha)
            if not trailers:
                continue
            turn_content = self._read_turn_tool_call(repo, sha, trailers)
            if turn_content and "path" in turn_content.get("tool_input", {}):
                path = turn_content["tool_input"]["path"]
                content = turn_content.get("content", "")
                content_hash = hashlib.sha256(content.encode()).hexdigest()
                self.set(branch, path, trailers.turn, content_hash)

    @staticmethod
    def _read_turn_tool_call(
        repo: GitRepo, sha: str, trailers: Any
    ) -> dict | None:
        turn_filename = repo.get_turn_filename(sha)
        if not turn_filename:
            return None
        try:
            raw = repo.get_turn_content(sha, turn_filename)
            return json.loads(raw)
        except (json.JSONDecodeError, Exception):
            return None


@dataclass
class ContextCacheEntry:
    branch: str
    turn_n: int
    messages: list[dict[str, Any]]


class ContextCache:
    def __init__(self):
        self._cache: dict[tuple[str, int], ContextCacheEntry] = {}

    def get(self, branch: str, turn_n: int) -> Optional[ContextCacheEntry]:
        return self._cache.get((branch, turn_n))

    def set(self, entry: ContextCacheEntry) -> None:
        self._cache[(entry.branch, entry.turn_n)] = entry

    def invalidate(self, branch: str) -> None:
        keys = [k for k in self._cache if k[0] == branch]
        for k in keys:
            del self._cache[k]


class ContextAssembler:
    def __init__(
        self,
        log_repo: GitRepo,
        config: SessionConfig,
        dedup_index: DedupIndex | None = None,
        cache: ContextCache | None = None,
    ):
        self.log_repo = log_repo
        self.config = config
        self.dedup_index = dedup_index or DedupIndex()
        self.cache = cache or ContextCache()

    def assemble(
        self,
        branch: str,
        up_to_turn: int | None = None,
        budget_tokens: int | None = None,
        rag_results: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        cached = self.cache.get(branch, up_to_turn or -1)
        if cached:
            return cached.messages

        commits = self.log_repo.log_branch(branch, format="%H", reverse=True)
        messages: list[dict[str, Any]] = []
        seen_summaries: dict[str, str] = {}

        for sha in commits:
            trailers = self.log_repo.parse_trailers(sha)
            if not trailers:
                continue
            if up_to_turn is not None and trailers.turn > up_to_turn:
                break

            turn = self._read_turn(self.log_repo, sha, trailers)
            if not turn:
                continue

            if turn.role == TurnRole.summary and turn.summarizes:
                for s in turn.summarizes:
                    seen_summaries[s] = turn.content

            if self._should_skip(turn, seen_summaries, sha):
                continue

            message = self._turn_to_message(turn, branch, sha, trailers, messages)
            if message:
                messages.append(message)

        messages = self._apply_dedup(branch, messages)
        messages = self._apply_budget(messages, budget_tokens)

        if rag_results:
            rag_message = {
                "role": "system",
                "content": self._format_rag_context(rag_results),
            }
            messages.insert(0, rag_message)

        if up_to_turn is not None:
            self.cache.set(ContextCacheEntry(
                branch=branch,
                turn_n=up_to_turn,
                messages=list(messages),
            ))

        return messages

    def _read_turn(self, repo: GitRepo, sha: str, trailers: Any) -> Optional[Turn]:
        turn_filename = repo.get_turn_filename(sha)
        if not turn_filename:
            return None
        try:
            raw = repo.get_turn_content(sha, turn_filename)
            data = json.loads(raw)
            return Turn(**data)
        except (json.JSONDecodeError, Exception):
            return None

    def _should_skip(
        self, turn: Turn, seen_summaries: dict[str, str], sha: str
    ) -> bool:
        if turn.role == TurnRole.summary:
            return True
        return sha in seen_summaries

    def _turn_to_message(
        self,
        turn: Turn,
        branch: str,
        sha: str,
        trailers: Any,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if turn.role == TurnRole.system:
            return {"role": "system", "content": turn.content}
        elif turn.role == TurnRole.user:
            return {"role": "user", "content": turn.content}
        elif turn.role == TurnRole.assistant:
            return {"role": "assistant", "content": turn.content}
        elif turn.role == TurnRole.tool_call:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_{sha[:12]}_{turn.turn}",
                        "type": "function",
                        "function": {
                            "name": turn.tool_name or "unknown",
                            "arguments": json.dumps(turn.tool_input or {}),
                        },
                    }
                ],
            }
        elif turn.role == TurnRole.tool_result:
            return {
                "role": "tool",
                "tool_call_id": f"call_{sha[:12]}_{turn.turn}",
                "content": turn.tool_output if turn.tool_output else turn.content,
            }
        return None

    def _apply_dedup(
        self, branch: str, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        read_index: dict[str, tuple[int, str]] = {}

        for msg in messages:
            if msg.get("role") == "tool" and "content" in msg:
                path = self._extract_path(msg)
                if path:
                    content = msg.get("content", "")
                    content_hash = hashlib.sha256(
                        content.encode() if isinstance(content, str) else str(content).encode()
                    ).hexdigest()

                    prev = read_index.get(path)
                    if prev and prev[1] == content_hash:
                        result.append({
                            "role": "tool",
                            "content": f"[see turn at {prev[0]} — content unchanged]",
                            "tool_call_id": msg.get("tool_call_id", ""),
                        })
                    else:
                        read_index[path] = (prev[0] + 1 if prev else 0, content_hash)
                        result.append(msg)
                else:
                    result.append(msg)
            else:
                result.append(msg)

        return result

    def _extract_path(self, msg: dict[str, Any]) -> Optional[str]:
        return None

    def _apply_budget(
        self,
        messages: list[dict[str, Any]],
        budget_tokens: int | None,
        approx_tokens_per_msg: int = 500,
    ) -> list[dict[str, Any]]:
        if budget_tokens is None:
            return messages

        total = 0
        result: list[dict[str, Any]] = []
        for msg in reversed(messages):
            content = msg.get("content", "")
            tokens = len(str(content)) // 4
            total += tokens
            if total > budget_tokens:
                break
            result.insert(0, msg)

        return result

    def _format_rag_context(self, results: list[dict[str, Any]]) -> str:
        parts = []
        for r in results:
            parts.append(f"[{r.get('type', 'doc')}] (score: {r.get('score', 0):.3f})\n{r.get('content', '')}")
        return "\n\n".join(parts)

    def compute_summary(
        self,
        branch: str,
        start_sha: str,
        end_sha: str,
        summary_content: str,
    ) -> Turn:
        commits = self.log_repo.log_branch(branch, format="%H", reverse=True)
        summarized: list[str] = []
        in_range = False
        for sha in commits:
            if sha == start_sha:
                in_range = True
            if in_range:
                summarized.append(sha)
            if sha == end_sha:
                break

        trailers = self.log_repo.parse_trailers(end_sha)
        turn_number = (trailers.turn if trailers else 0) + 1

        return Turn(
            turn=turn_number,
            role=TurnRole.summary,
            content=summary_content,
            agent_id=branch.split("/")[-1],
            summarizes=summarized,
        )
