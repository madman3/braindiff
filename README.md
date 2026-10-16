# MemEval — does agent memory actually help?

Small eval harness: same coding agent, real historical [LangGraph](https://github.com/langchain-ai/langgraph) bugs, three memory conditions, scored with the repo's own tests (no LLM judge).

## Setup

```bash
# clone target once
git clone https://github.com/langchain-ai/langgraph.git /workspace/langgraph-src

cd memeval
uv sync
export MEMEVAL_LANGGRAPH_REPO=/workspace/langgraph-src
```

## Commands

```bash
uv run memeval validate T1          # parent fails, gold fix passes
uv run memeval run T2 --condition none   # needs agent backend (ask before wiring)
uv run memeval experiment pair-a
uv run memeval report
```

## Hard requirements

- `PYTEST_ADDOPTS='-k "not postgres"'` via env (never shell-split `-k`)
- Each workspace gets its own venv; verify `import langgraph.pregel` points inside it
- Agent workspaces are `git archive` trees **without** `.git`
- Unset proxy env vars when running tests

## Agent backend

**Claude Agent SDK** (`claude-agent-sdk`, bundled Claude Code CLI). Requires `ANTHROPIC_API_KEY`.

## Executors

| `--executor` | Isolation | Notes |
| --- | --- | --- |
| `local` (default) | Fresh `git archive` temp dir + own `.venv` | Must work first |
| `daytona` | Daytona sandbox per attempt | Needs `DAYTONA_API_KEY`; good for parallel A/B of two conditions in separate VMs |

Example dual-condition idea (after keys work):

```bash
# Terminal A
uv run memeval run T2 --condition none --executor daytona --seed 0
# Terminal B  
uv run memeval run T2 --condition agents_md+memory --lessons-from T1 --executor daytona --seed 0
uv run memeval report
```

## Status

- Milestone 1: `validate T1` OK (3 fail → 3 pass)
- Milestone 2: Claude Agent SDK wired; **blocked on `ANTHROPIC_API_KEY` in this environment**
- Daytona executor scaffolded; **blocked on `DAYTONA_API_KEY`** for live sandboxes
