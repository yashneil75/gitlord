from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from gitlord.schemas import Turn, TurnRole, SessionConfig
from gitlord.git import GitRepo

try:
    import tiktoken
    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False


def count_tokens(text: str) -> int:
    if HAS_TIKTOKEN:
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    return len(text) // 4


@dataclass
class DedupIndex:
    _map: dict[tuple[str, str], tuple[int, str]] = field(default_factory=dict)

    def get(self, branch: str, path: str) -> tuple[int, str] | None:
        return self._map.get((branch, path))

    def set(self, branch: str, path: str, turn: int, content_hash: str) -> None:
        self._map[(branch, path)] = (turn, content_hash)

    def invalidate(self, branch: str, path: str) -> None:
        self._map.pop((branch, path), None)

    def invalidate_branch(self, branch: str) -> None:
        keys = [k for k in self._map if k[0] == branch]
        for k in keys:
            del self._map[k]

    def rebuild_from_log(
        self,
        repo: GitRepo,
        branch: str,
        max_commits: int = 1000,
    ) -> None:
        commits = repo.log_branch(branch, format="%H", reverse=True)
        for i, sha in enumerate(commits[-max_commits:]):
            trailers = repo.parse_trailers(sha)
            if not trailers:
                continue
            turn_data = self._read_turn_json(repo, sha)
            if not turn_data:
                continue
            if turn_data.get("role") != "tool_call":
                continue
            tool_input = turn_data.get("tool_input")
            if not isinstance(tool_input, dict):
                continue
            path = tool_input.get("path")
            if not path:
                continue
            for j in range(i + 1, min(i + 5, len(commits))):
                next_sha = commits[j]
                next_trailers = repo.parse_trailers(next_sha)
                if not next_trailers:
                    continue
                next_turn = self._read_turn_json(repo, next_sha)
                if next_turn and next_turn.get("role") == "tool_result":
                    result_content = (
                        next_turn.get("content")
                        or next_turn.get("tool_output")
                        or ""
                    )
                    content_hash = hashlib.sha256(
                        str(result_content).encode()
                    ).hexdigest()
                    self.set(branch, path, trailers.turn, content_hash)
                    break

    @staticmethod
    def _read_turn_json(repo: GitRepo, sha: str) -> dict | None:
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
    def __init__(self) -> None:
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
    ) -> None:
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
        cache_key = up_to_turn if up_to_turn is not None else -1
        cached = self.cache.get(branch, cache_key)
        if cached:
            return cached.messages

        commits = self.log_repo.log_branch(branch, format="%H", reverse=True)

        collected: list[tuple[Turn, str]] = []
        summary_exclusions: dict[str, str] = {}

        for sha in commits:
            trailers = self.log_repo.parse_trailers(sha)
            if not trailers:
                continue
            if up_to_turn is not None and trailers.turn > up_to_turn:
                break

            turn = self._read_turn(self.log_repo, sha)
            if not turn:
                continue

            if turn.role == TurnRole.summary and turn.summarizes:
                for s in turn.summarizes:
                    summary_exclusions[s] = turn.content
            else:
                collected.append((turn, sha))

        messages: list[dict[str, Any]] = []
        last_tool_call_turn: int | None = None
        for turn, sha in collected:
            if sha in summary_exclusions:
                continue
            if turn.role == TurnRole.tool_call:
                last_tool_call_turn = turn.turn
            message = self._turn_to_message(turn, sha, last_tool_call_turn)
            if turn.role == TurnRole.tool_result:
                last_tool_call_turn = None
            if message:
                messages.append(message)

        messages = self._apply_dedup(messages)
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
                messages=messages,
            ))

        return messages

    def _read_turn(self, repo: GitRepo, sha: str) -> Optional[Turn]:
        turn_filename = repo.get_turn_filename(sha)
        if not turn_filename:
            return None
        try:
            raw = repo.get_turn_content(sha, turn_filename)
            data = json.loads(raw)
            return Turn(**data)
        except (json.JSONDecodeError, Exception):
            return None

    def _turn_to_message(
        self,
        turn: Turn,
        sha: str,
        last_tool_call_turn: int | None = None,
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
                        "id": f"call_t{turn.turn}",
                        "type": "function",
                        "function": {
                            "name": turn.tool_name or "unknown",
                            "arguments": json.dumps(turn.tool_input or {}),
                        },
                    }
                ],
            }
        elif turn.role == TurnRole.tool_result:
            # pair with the most recent unanswered tool_call; turns are not
            # guaranteed to be adjacent (assistant text can interleave)
            paired_turn = (
                last_tool_call_turn
                if last_tool_call_turn is not None
                else turn.turn - 1
            )
            return {
                "role": "tool",
                "tool_call_id": f"call_t{paired_turn}",
                "content": turn.tool_output if turn.tool_output else turn.content,
            }
        return None

    @staticmethod
    def _parse_turn_from_call_id(call_id: str) -> int:
        if call_id.startswith("call_t"):
            rest = call_id.removeprefix("call_t")
            try:
                return int(rest)
            except ValueError:
                pass
        return 0

    def _apply_dedup(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        call_paths: dict[str, tuple[str, int]] = {}
        read_index: dict[str, tuple[int, str]] = {}

        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    args = json.loads(tc["function"]["arguments"])
                    if isinstance(args, dict):
                        path = args.get("path")
                        if path:
                            turn_n = self._parse_turn_from_call_id(tc["id"])
                            call_paths[tc["id"]] = (path, turn_n)
                result.append(msg)

            elif msg.get("role") == "tool":
                tc_id = msg.get("tool_call_id", "")
                if tc_id in call_paths:
                    path, turn_n = call_paths[tc_id]
                    content = msg.get("content", "")
                    content_hash = hashlib.sha256(
                        str(content).encode()
                    ).hexdigest()

                    prev = read_index.get(path)
                    if prev and prev[1] == content_hash:
                        result.append({
                            "role": "tool",
                            "content": f"[see turn {prev[0]} — content unchanged]",
                            "tool_call_id": tc_id,
                        })
                    else:
                        read_index[path] = (turn_n, content_hash)
                        result.append(msg)
                else:
                    result.append(msg)

            else:
                result.append(msg)

        return result

    def _apply_budget(
        self,
        messages: list[dict[str, Any]],
        budget_tokens: int | None,
    ) -> list[dict[str, Any]]:
        if budget_tokens is None:
            return messages

        total = 0
        result: list[dict[str, Any]] = []
        for msg in reversed(messages):
            content = msg.get("content") or ""
            tokens = count_tokens(str(content))
            total += tokens
            if total > budget_tokens:
                break
            result.insert(0, msg)

        return result

    def _format_rag_context(self, results: list[dict[str, Any]]) -> str:
        parts = []
        for r in results:
            parts.append(
                f"[{r.get('type', 'doc')}] (score: {r.get('score', 0):.3f})\n"
                f"{r.get('content', '')}"
            )
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
