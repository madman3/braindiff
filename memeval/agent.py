"""Pluggable coding agent interface + Claude Agent SDK implementation."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class Budget:
    max_turns: int = 40
    timeout_s: int = 600
    model: str = "claude-sonnet-4-5"
    temperature: float = 0.0  # logged; SDK uses model defaults
    max_budget_usd: float | None = 5.0


@dataclass
class AttemptResult:
    transcript: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    turns: int = 0
    wall_time_s: float = 0.0
    exit_reason: str = "ok"
    error: str | None = None
    session_id: str | None = None
    model: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class Agent(Protocol):
    def solve(
        self,
        workspace: Path,
        problem: str,
        extra_context: str,
        budget: Budget,
    ) -> AttemptResult: ...


def _serialize_message(msg: Any) -> dict:
    """Best-effort JSON-friendly transcript entry."""
    out: dict[str, Any] = {"type": type(msg).__name__}
    for attr in (
        "subtype",
        "session_id",
        "is_error",
        "num_turns",
        "total_cost_usd",
        "usage",
        "result",
        "stop_reason",
        "duration_ms",
        "errors",
    ):
        if hasattr(msg, attr):
            val = getattr(msg, attr)
            if val is not None:
                out[attr] = val
    if hasattr(msg, "content"):
        blocks = []
        for b in msg.content or []:
            btype = type(b).__name__
            if hasattr(b, "text"):
                blocks.append({"type": btype, "text": b.text})
            elif hasattr(b, "name"):
                blocks.append({"type": btype, "name": getattr(b, "name", None), "id": getattr(b, "id", None)})
            else:
                blocks.append({"type": btype})
        out["content"] = blocks
    return out


class ClaudeAgentSDKAgent:
    """Coding agent via Anthropic Claude Agent SDK (`query` one-shot session)."""

    def __init__(self, *, cli_path: str | Path | None = None) -> None:
        self.cli_path = cli_path

    def solve(
        self,
        workspace: Path,
        problem: str,
        extra_context: str,
        budget: Budget,
    ) -> AttemptResult:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return AttemptResult(
                exit_reason="missing_api_key",
                error="ANTHROPIC_API_KEY is not set in the environment",
                model=budget.model,
            )
        return asyncio.run(self._asolve(workspace, problem, extra_context, budget))

    async def _asolve(
        self,
        workspace: Path,
        problem: str,
        extra_context: str,
        budget: Budget,
    ) -> AttemptResult:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            query,
        )

        system = (
            "You are a coding agent fixing a real bug in an isolated LangGraph checkout.\n"
            "Constraints:\n"
            "- Only modify library source under libs/langgraph/langgraph/ as needed.\n"
            "- Do NOT edit or delete the task test files to make them pass.\n"
            "- Do NOT access the network, git remotes, or install packages.\n"
            "- Run the provided failing tests with: "
            "cd libs/langgraph && PYTEST_ADDOPTS='-k \"not postgres\"' uv run pytest <testfile> -v\n"
            "- Prefer a minimal correct fix. Stop when the task tests pass.\n"
        )
        if extra_context.strip():
            system += "\n" + extra_context.strip() + "\n"

        prompt = (
            "Fix the following bug in this workspace.\n\n"
            f"{problem.strip()}\n\n"
            "Workspace root is the current directory. Investigate, edit source, and verify with the tests."
        )

        options = ClaudeAgentOptions(
            cwd=str(workspace),
            model=budget.model,
            max_turns=budget.max_turns,
            max_budget_usd=budget.max_budget_usd,
            permission_mode="acceptEdits",
            allowed_tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
            # Do not pull user/project Claude settings that could leak fixes.
            setting_sources=[],
            system_prompt=system,
            env={
                k: v
                for k, v in os.environ.items()
                if k in ("ANTHROPIC_API_KEY", "PATH", "HOME", "LANG", "LC_ALL", "TERM")
            },
        )
        if self.cli_path:
            options.cli_path = str(self.cli_path)

        transcript: list[dict] = []
        result = AttemptResult(model=budget.model)
        t0 = time.perf_counter()
        try:
            async with asyncio.timeout(budget.timeout_s):
                async for message in query(prompt=prompt, options=options):
                    transcript.append(_serialize_message(message))
                    if isinstance(message, ResultMessage):
                        result.turns = message.num_turns or 0
                        result.cost_usd = message.total_cost_usd
                        result.session_id = message.session_id
                        usage = message.usage or {}
                        result.input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
                        result.output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
                        if message.is_error:
                            result.exit_reason = "error"
                            result.error = "; ".join(message.errors or []) or message.result
                        else:
                            result.exit_reason = message.stop_reason or "completed"
                    elif isinstance(message, AssistantMessage):
                        pass
        except TimeoutError:
            result.exit_reason = "timeout"
            result.error = f"exceeded budget.timeout_s={budget.timeout_s}"
        except Exception as e:  # noqa: BLE001 — surface to harness
            result.exit_reason = "exception"
            result.error = f"{type(e).__name__}: {e}"
        result.wall_time_s = time.perf_counter() - t0
        result.transcript = transcript
        return result


def get_default_agent() -> ClaudeAgentSDKAgent:
    return ClaudeAgentSDKAgent()
