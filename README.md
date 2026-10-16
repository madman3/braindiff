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

# required for live agent runs
export ANTHROPIC_API_KEY=...
# optional dual isolated sandboxes
export DAYTONA_API_KEY=...
```

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
- Claude Agent SDK wired; live runs need `ANTHROPIC_API_KEY`
- Daytona executor scaffolded; needs `DAYTONA_API_KEY`
