# BrainDiff

Does agent memory actually help?

BrainDiff’s first experiment harness (**MemEval**): the same coding agent on real historical [LangGraph](https://github.com/langchain-ai/langgraph) bugs, under different memory conditions, scored with the repo’s own tests (no LLM judge).

Home: https://github.com/madman3/braindiff

## Setup

```bash
git clone https://github.com/madman3/braindiff.git
cd braindiff
uv sync

# clone the benchmark target once
git clone https://github.com/langchain-ai/langgraph.git ../langgraph-src
export MEMEVAL_LANGGRAPH_REPO="$(pwd)/../langgraph-src"

# required for live agent runs (workspace-scoped Console key; API key only)
export ANTHROPIC_API_KEY=...
# optional dual isolated sandboxes
export DAYTONA_API_KEY=...
```

MemEval talks to Anthropic via the **Messages API** (`ANTHROPIC_API_KEY` only). It does **not** send or require `ANTHROPIC_WORKSPACE_ID` / `anthropic-workspace-id`. If you see a workspace-scoping error, create a key scoped to a single workspace in the Claude Console and replace `ANTHROPIC_API_KEY`.


## Commands

```bash
uv run memeval validate T1
uv run memeval run T1 --condition none
uv run memeval run T2 --condition agents_md+memory --lessons-from T1
uv run memeval experiment pair-a
uv run memeval report
```

## Conditions

1. `none` — problem + repo; `AGENTS.md` removed  
2. `agents_md` — repo as-is  
3. `agents_md+memory` — maintainer `AGENTS.md` + lessons from train tasks  

## Executors

| `--executor` | Isolation |
| --- | --- |
| `local` (default) | Fresh `git archive` temp dir + own `.venv` |
| `daytona` | Daytona sandbox per attempt (parallel A/B) |

## Hard requirements

- `PYTEST_ADDOPTS='-k "not postgres"'` via env (never shell-split `-k`)
- Each workspace gets its own venv; `import langgraph.pregel` must point inside it
- Agent workspaces are `git archive` trees **without** `.git`
- Unset proxy env vars when running tests

## Status

- `validate T1`: OK (3 fail → 3 pass on gold)
- Default agent: Messages API tool loop (`ANTHROPIC_API_KEY` only)
- Claude Agent SDK backend still available for experiments
- Daytona executor scaffolded; needs `DAYTONA_API_KEY`
- Milestone 1 harness hardening: `MEMEVAL_LANGGRAPH_REPO` missing-path fallback; agent Bash env allowlist + blocked `git clone`/`gh`; clean patch capture (`diff_files_changed` / `±` lines, `suspicious_empty_diff`); overlaid test-file tamper detection (`test_tampering`)
- First scored attempt `T1 --condition none` (`results/T1-none-s0-20261016T200737Z.json`): **`exit_reason=auth_error`** — the `ANTHROPIC_API_KEY` in `.env` is a multi-workspace user key and the Messages API rejected it with HTTP 400 (`not scoped to a workspace`). No model turns ran (`turns=0`, `resolved=false`, `partial=0.0`). Replace with a **single-workspace-scoped** Console API key; do not add `anthropic-workspace-id` / `ANTHROPIC_WORKSPACE_ID`.
