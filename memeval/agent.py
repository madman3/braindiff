"""Pluggable coding agent interface + Messages API / Claude Agent SDK backends."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class Budget:
    max_turns: int = 40
    timeout_s: int = 600
    model: str = "claude-sonnet-4-5"
    temperature: float = 0.0  # logged; backends may use model defaults
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


_SYSTEM = (
    "You are a coding agent fixing a real bug in an isolated LangGraph checkout.\n"
    "Constraints:\n"
    "- Only modify library source under libs/langgraph/langgraph/ as needed.\n"
    "- Do NOT edit or delete the task test files to make them pass.\n"
    "- Do NOT access the network, git remotes, or install packages.\n"
    "- Run the provided failing tests with: "
    "cd libs/langgraph && PYTEST_ADDOPTS='-k \"not postgres\"' uv run pytest <testfile> -v\n"
    "- Prefer a minimal correct fix. Stop when the task tests pass.\n"
)

_WORKSPACE_SCOPED_KEY_HINT = (
    "This Anthropic API key is not scoped to a single workspace. "
    "Create a workspace-scoped key in the Claude Console "
    "(Settings → API keys → create key scoped to Default Workspace) "
    "and set ANTHROPIC_API_KEY to that key. "
    "MemEval does not require or send anthropic-workspace-id."
)


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
                blocks.append(
                    {
                        "type": btype,
                        "name": getattr(b, "name", None),
                        "id": getattr(b, "id", None),
                    }
                )
            else:
                blocks.append({"type": btype})
        out["content"] = blocks
    return out


def _require_api_key(budget: Budget) -> AttemptResult | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return AttemptResult(
            exit_reason="missing_api_key",
            error="ANTHROPIC_API_KEY is not set in the environment",
            model=budget.model,
        )
    return None


def _is_workspace_scope_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "anthropic-workspace-id" in text or (
        "not scoped to a workspace" in text and "workspace" in text
    )


# ---------------------------------------------------------------------------
# Messages API tool-loop agent (default)
# ---------------------------------------------------------------------------

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "Read",
        "description": "Read a UTF-8 text file under the workspace. Paths are relative to workspace root.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to read"},
                "offset": {
                    "type": "integer",
                    "description": "1-indexed start line (optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max lines to return (optional)",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "Write",
        "description": "Create or overwrite a UTF-8 text file under the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "Edit",
        "description": "Replace exactly one occurrence of old_string with new_string in a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "Glob",
        "description": "List files matching a glob pattern under the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Grep",
        "description": "Search file contents with a regex (ripgrep-like). Paths relative to workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {
                    "type": "string",
                    "description": "Subpath to search (default: workspace root)",
                },
                "glob": {"type": "string", "description": "Optional filename glob"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Bash",
        "description": (
            "Run a shell command with cwd=workspace root. "
            "No network. Prefer uv run pytest for tests."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_s": {
                    "type": "integer",
                    "description": "Command timeout seconds (default 120, max 300)",
                },
            },
            "required": ["command"],
        },
    },
]


class _WorkspaceTools:
    """Read/Write/Edit/Bash/Glob/Grep scoped strictly under workspace root."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def resolve(self, rel: str) -> Path:
        raw = (rel or "").strip()
        if not raw:
            raise ValueError("empty path")
        p = Path(raw)
        if p.is_absolute():
            cand = p.resolve()
        else:
            cand = (self.root / p).resolve()
        try:
            cand.relative_to(self.root)
        except ValueError as e:
            raise ValueError(f"path escapes workspace: {rel}") from e
        return cand

    def read(self, path: str, offset: int | None = None, limit: int | None = None) -> str:
        target = self.resolve(path)
        if not target.is_file():
            raise FileNotFoundError(f"not a file: {path}")
        text = target.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        start = 0
        if offset is not None:
            start = max(0, int(offset) - 1)
        end = len(lines) if limit is None else start + max(0, int(limit))
        chunk = lines[start:end]
        numbered = []
        for i, line in enumerate(chunk, start=start + 1):
            numbered.append(f"{i:>6}|{line.rstrip(chr(10))}")
        return "\n".join(numbered) if numbered else "(empty)"

    def write(self, path: str, content: str) -> str:
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"

    def edit(self, path: str, old_string: str, new_string: str) -> str:
        target = self.resolve(path)
        text = target.read_text(encoding="utf-8")
        count = text.count(old_string)
        if count == 0:
            raise ValueError("old_string not found")
        if count > 1:
            raise ValueError(f"old_string matched {count} times; must be unique")
        target.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
        return f"Edited {path}"

    def glob(self, pattern: str) -> str:
        matches = sorted(
            str(p.relative_to(self.root))
            for p in self.root.glob(pattern)
            if ".venv" not in p.parts and p.is_file()
        )
        if not matches:
            return "(no matches)"
        return "\n".join(matches[:500])

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> str:
        base = self.resolve(path) if path else self.root
        regex = re.compile(pattern)
        hits: list[str] = []
        files: list[Path]
        if base.is_file():
            files = [base]
        else:
            files = [
                p
                for p in (base.rglob(glob) if glob else base.rglob("*"))
                if p.is_file() and ".venv" not in p.parts and "__pycache__" not in p.parts
            ]
        for fp in files[:2000]:
            try:
                for i, line in enumerate(fp.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if regex.search(line):
                        rel = fp.relative_to(self.root)
                        hits.append(f"{rel}:{i}:{line[:400]}")
                        if len(hits) >= 200:
                            return "\n".join(hits) + "\n...(truncated)"
            except OSError:
                continue
        return "\n".join(hits) if hits else "(no matches)"

    def bash(self, command: str, timeout_s: int = 120) -> str:
        timeout_s = max(1, min(int(timeout_s or 120), 300))
        # Soft guardrails: no network installers / remotes
        lowered = command.lower()
        for banned in ("curl ", "wget ", "pip install", "uv add", "uv pip install", "npm install"):
            if banned in lowered:
                return f"ERROR: blocked command pattern {banned!r}"
        env = os.environ.copy()
        env.setdefault("PYTEST_ADDOPTS", '-k "not postgres"')
        # Drop proxy vars that break local pytest / uv in some environments
        for k in list(env):
            if k.lower().endswith("_proxy"):
                env.pop(k, None)
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(self.root),
                text=True,
                capture_output=True,
                timeout=timeout_s,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return f"ERROR: command timed out after {timeout_s}s"
        out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        if len(out) > 80_000:
            out = out[:40_000] + "\n...\n" + out[-40_000:]
        return f"exit={proc.returncode}\n{out}"

    def dispatch(self, name: str, args: dict[str, Any]) -> str:
        try:
            if name == "Read":
                return self.read(args.get("path", ""), args.get("offset"), args.get("limit"))
            if name == "Write":
                return self.write(args.get("path", ""), args.get("content", ""))
            if name == "Edit":
                return self.edit(args.get("path", ""), args.get("old_string", ""), args.get("new_string", ""))
            if name == "Glob":
                return self.glob(args.get("pattern", "*"))
            if name == "Grep":
                return self.grep(args.get("pattern", ""), args.get("path"), args.get("glob"))
            if name == "Bash":
                return self.bash(args.get("command", ""), int(args.get("timeout_s") or 120))
            return f"ERROR: unknown tool {name}"
        except Exception as e:  # noqa: BLE001 — return to model
            return f"ERROR: {type(e).__name__}: {e}"


class MessagesAPIAgent:
    """Minimal Anthropic Messages API tool loop (Read/Write/Edit/Bash/Glob/Grep).

    Auth: ``ANTHROPIC_API_KEY`` only. Does not send ``anthropic-workspace-id``.
    Multi-workspace keys must be replaced with a workspace-scoped key.
    """

    def solve(
        self,
        workspace: Path,
        problem: str,
        extra_context: str,
        budget: Budget,
    ) -> AttemptResult:
        missing = _require_api_key(budget)
        if missing:
            return missing

        import anthropic

        system = _SYSTEM
        if extra_context.strip():
            system += "\n" + extra_context.strip() + "\n"

        prompt = (
            "Fix the following bug in this workspace.\n\n"
            f"{problem.strip()}\n\n"
            "Workspace root is the current directory. Investigate, edit source, and verify with the tests."
        )

        tools = _WorkspaceTools(workspace)
        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY; no workspace header
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        transcript: list[dict] = []
        result = AttemptResult(model=budget.model)
        t0 = time.perf_counter()
        turns = 0

        try:
            deadline = t0 + budget.timeout_s
            while turns < budget.max_turns:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    result.exit_reason = "timeout"
                    result.error = f"exceeded budget.timeout_s={budget.timeout_s}"
                    break

                kwargs: dict[str, Any] = {
                    "model": budget.model,
                    "max_tokens": 8192,
                    "system": system,
                    "messages": messages,
                    "tools": _TOOLS,
                }
                # Do not pass workspace_id — Messages API + workspace-scoped keys omit it.
                try:
                    resp = client.messages.create(**kwargs, timeout=min(remaining, 120.0))
                except anthropic.BadRequestError as e:
                    if _is_workspace_scope_error(e):
                        result.exit_reason = "auth_error"
                        result.error = _WORKSPACE_SCOPED_KEY_HINT + f" (API: {e})"
                        transcript.append({"type": "AuthError", "error": str(e)})
                        break
                    raise

                turns += 1
                usage = getattr(resp, "usage", None)
                if usage is not None:
                    result.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
                    result.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)

                content_blocks = list(resp.content or [])
                assistant_content: list[dict[str, Any]] = []
                tool_uses: list[Any] = []
                text_parts: list[str] = []
                for block in content_blocks:
                    btype = getattr(block, "type", None)
                    if btype == "text":
                        text_parts.append(block.text)
                        assistant_content.append({"type": "text", "text": block.text})
                    elif btype == "tool_use":
                        tool_uses.append(block)
                        assistant_content.append(
                            {
                                "type": "tool_use",
                                "id": block.id,
                                "name": block.name,
                                "input": block.input,
                            }
                        )
                    else:
                        assistant_content.append({"type": str(btype)})

                transcript.append(
                    {
                        "type": "AssistantMessage",
                        "stop_reason": resp.stop_reason,
                        "content": [
                            (
                                {"type": "text", "text": b.text}
                                if getattr(b, "type", None) == "text"
                                else {
                                    "type": "tool_use",
                                    "name": getattr(b, "name", None),
                                    "id": getattr(b, "id", None),
                                }
                            )
                            for b in content_blocks
                        ],
                    }
                )
                messages.append({"role": "assistant", "content": assistant_content})

                if resp.stop_reason == "end_turn" or not tool_uses:
                    result.exit_reason = resp.stop_reason or "completed"
                    break

                tool_results: list[dict[str, Any]] = []
                for tu in tool_uses:
                    args = tu.input if isinstance(tu.input, dict) else {}
                    out = tools.dispatch(tu.name, args)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": out,
                        }
                    )
                    transcript.append(
                        {
                            "type": "ToolResult",
                            "name": tu.name,
                            "tool_use_id": tu.id,
                            "content_preview": out[:2000],
                        }
                    )
                messages.append({"role": "user", "content": tool_results})
            else:
                if result.exit_reason == "ok":
                    result.exit_reason = "max_turns"
                    result.error = f"reached budget.max_turns={budget.max_turns}"
        except TimeoutError:
            result.exit_reason = "timeout"
            result.error = f"exceeded budget.timeout_s={budget.timeout_s}"
        except Exception as e:  # noqa: BLE001
            if _is_workspace_scope_error(e):
                result.exit_reason = "auth_error"
                result.error = _WORKSPACE_SCOPED_KEY_HINT + f" (API: {e})"
            else:
                result.exit_reason = "exception"
                result.error = f"{type(e).__name__}: {e}"

        result.turns = turns
        result.wall_time_s = time.perf_counter() - t0
        result.transcript = transcript
        return result


# ---------------------------------------------------------------------------
# Claude Agent SDK (optional; uses bundled Claude Code CLI)
# ---------------------------------------------------------------------------


class ClaudeAgentSDKAgent:
    """Coding agent via Anthropic Claude Agent SDK (`query` one-shot session).

    Prefer ``MessagesAPIAgent`` for ``sk-ant-*`` keys: the bundled Claude Code CLI
    path often injects workspace scoping requirements. This backend still only
    forwards ``ANTHROPIC_API_KEY`` (no workspace header / env).
    """

    def __init__(self, *, cli_path: str | Path | None = None) -> None:
        self.cli_path = cli_path

    def solve(
        self,
        workspace: Path,
        problem: str,
        extra_context: str,
        budget: Budget,
    ) -> AttemptResult:
        missing = _require_api_key(budget)
        if missing:
            return missing
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

        system = _SYSTEM
        if extra_context.strip():
            system += "\n" + extra_context.strip() + "\n"

        prompt = (
            "Fix the following bug in this workspace.\n\n"
            f"{problem.strip()}\n\n"
            "Workspace root is the current directory. Investigate, edit source, and verify with the tests."
        )

        # Forward Anthropic API key only — no workspace-id header/env.
        _forward = (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "PATH",
            "HOME",
            "LANG",
            "LC_ALL",
            "TERM",
        )
        options = ClaudeAgentOptions(
            cwd=str(workspace),
            model=budget.model,
            max_turns=budget.max_turns,
            max_budget_usd=budget.max_budget_usd,
            permission_mode="acceptEdits",
            allowed_tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
            setting_sources=[],
            system_prompt=system,
            env={k: os.environ[k] for k in _forward if os.environ.get(k)},
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
                        result.input_tokens = int(
                            usage.get("input_tokens") or usage.get("prompt_tokens") or 0
                        )
                        result.output_tokens = int(
                            usage.get("output_tokens") or usage.get("completion_tokens") or 0
                        )
                        if message.is_error:
                            err = "; ".join(message.errors or []) or message.result or ""
                            if _is_workspace_scope_error(Exception(err)):
                                result.exit_reason = "auth_error"
                                result.error = _WORKSPACE_SCOPED_KEY_HINT + f" (CLI: {err})"
                            else:
                                result.exit_reason = "error"
                                result.error = err
                        else:
                            result.exit_reason = message.stop_reason or "completed"
                    elif isinstance(message, AssistantMessage):
                        pass
        except TimeoutError:
            result.exit_reason = "timeout"
            result.error = f"exceeded budget.timeout_s={budget.timeout_s}"
        except Exception as e:  # noqa: BLE001 — surface to harness
            if _is_workspace_scope_error(e):
                result.exit_reason = "auth_error"
                result.error = _WORKSPACE_SCOPED_KEY_HINT + f" (exception: {e})"
            else:
                result.exit_reason = "exception"
                result.error = f"{type(e).__name__}: {e}"
        result.wall_time_s = time.perf_counter() - t0
        result.transcript = transcript
        return result


def get_default_agent() -> MessagesAPIAgent:
    """Default: Messages API tool loop (API key only; no workspace header)."""
    return MessagesAPIAgent()
