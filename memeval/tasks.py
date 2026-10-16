"""Task registry for pre-validated LangGraph DeltaChannel / update_state bugs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Short hashes from the experiment design; resolve with git rev-parse against the clone.
TASK_SHORT_COMMITS: dict[str, str] = {
    "T1": "5d5a641",
    "T2": "b7d053b",
    "T3": "b45d96b",
    "T4": "7d0b33c",
    "T5": "1b84cb0",
    "T6": "456e2b1",
    "T7": "39c523e",
    "T8": "b4991f1",
    "T9": "3a1796e",
}


@dataclass(frozen=True)
class Task:
    id: str
    short_commit: str
    # Relative to monorepo root. Filled lazily after first resolve for known tasks.
    test_files: tuple[str, ...]
    src_files: tuple[str, ...]


# Paths discovered from `git show --name-status` for each fix commit.
# Only paths under libs/langgraph that are source (not tests) go in src_files.
TASKS: dict[str, Task] = {
    "T1": Task(
        id="T1",
        short_commit="5d5a641",
        test_files=("libs/langgraph/tests/test_delta_channel_id_stability.py",),
        src_files=(
            "libs/langgraph/langgraph/pregel/_loop.py",
            "libs/langgraph/langgraph/pregel/_messages.py",
        ),
    ),
    "T2": Task(
        id="T2",
        short_commit="b7d053b",
        test_files=(),  # resolved on demand
        src_files=(),
    ),
    "T3": Task(id="T3", short_commit="b45d96b", test_files=(), src_files=()),
    "T4": Task(id="T4", short_commit="7d0b33c", test_files=(), src_files=()),
    "T5": Task(id="T5", short_commit="1b84cb0", test_files=(), src_files=()),
    "T6": Task(id="T6", short_commit="456e2b1", test_files=(), src_files=()),
    "T7": Task(id="T7", short_commit="39c523e", test_files=(), src_files=()),
    "T8": Task(id="T8", short_commit="b4991f1", test_files=(), src_files=()),
    "T9": Task(id="T9", short_commit="3a1796e", test_files=(), src_files=()),
}


def get_task(task_id: str) -> Task:
    try:
        return TASKS[task_id.upper()]
    except KeyError as e:
        known = ", ".join(sorted(TASKS))
        raise SystemExit(f"Unknown task {task_id!r}. Known: {known}") from e


def resolve_commit(repo: Path, short: str) -> str:
    import subprocess

    out = subprocess.check_output(
        ["git", "rev-parse", short],
        cwd=repo,
        text=True,
    ).strip()
    return out


def resolve_parent(repo: Path, commit: str) -> str:
    import subprocess

    return subprocess.check_output(
        ["git", "rev-parse", f"{commit}^"],
        cwd=repo,
        text=True,
    ).strip()


def classify_commit_paths(repo: Path, commit: str) -> tuple[list[str], list[str]]:
    """Split commit paths into (src_files, test_files) under libs/langgraph."""
    import subprocess

    raw = subprocess.check_output(
        ["git", "show", "--name-only", "--pretty=format:", commit],
        cwd=repo,
        text=True,
    )
    paths = [p.strip() for p in raw.splitlines() if p.strip()]
    src: list[str] = []
    tests: list[str] = []
    for p in paths:
        if not p.startswith("libs/langgraph/"):
            continue
        if "/tests/" in p or p.endswith("_test.py"):
            tests.append(p)
        else:
            src.append(p)
    return src, tests


def ensure_task_paths(repo: Path, task: Task) -> Task:
    """Fill src/test paths from the fix commit when not hard-coded."""
    if task.test_files and task.src_files:
        return task
    commit = resolve_commit(repo, task.short_commit)
    src, tests = classify_commit_paths(repo, commit)
    if not tests:
        raise SystemExit(f"{task.id}: no test files found in commit {commit}")
    if not src:
        raise SystemExit(f"{task.id}: no source files found in commit {commit}")
    filled = Task(
        id=task.id,
        short_commit=task.short_commit,
        test_files=tuple(tests),
        src_files=tuple(src),
    )
    TASKS[task.id] = filled
    return filled
