"""Build isolated workspaces from parent snapshots + overlaid tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from memeval.tasks import Task, resolve_commit, resolve_parent


@dataclass
class Workspace:
    root: Path  # monorepo root (extracted tree)
    package_dir: Path  # libs/langgraph
    parent_sha: str
    fix_sha: str
    owns_dir: bool = True

    def cleanup(self) -> None:
        if self.owns_dir and self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)


def default_repo() -> Path:
    env = os.environ.get("MEMEVAL_LANGGRAPH_REPO")
    if env:
        return Path(env).resolve()
    # Prefer sibling clone next to memeval project
    candidates = [
        Path("/workspace/langgraph-src"),
        Path.cwd().parent / "langgraph-src",
        Path.cwd() / "langgraph-src",
    ]
    for c in candidates:
        if (c / ".git").exists():
            return c.resolve()
    raise SystemExit(
        "LangGraph clone not found. Set MEMEVAL_LANGGRAPH_REPO or clone to /workspace/langgraph-src"
    )


def git_show_file(repo: Path, commit: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=repo)


def extract_tree(repo: Path, commit: str, dest: Path) -> None:
    """Materialize commit tree without .git (no answer leakage via history)."""
    dest.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        ["git", "archive", commit],
        cwd=repo,
        stdout=subprocess.PIPE,
    )
    assert proc.stdout is not None
    subprocess.check_call(["tar", "-x"], cwd=dest, stdin=proc.stdout)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"git archive {commit} failed with {proc.returncode}")
    if (dest / ".git").exists():
        shutil.rmtree(dest / ".git")


def ensure_package_venv(package_dir: Path) -> None:
    """Create/sync the package venv in-place for this checkout only."""
    env = os.environ.copy()
    # Avoid inheriting memeval's VIRTUAL_ENV (uv would warn and confuse editables).
    env.pop("VIRTUAL_ENV", None)
    subprocess.check_call(
        ["uv", "sync", "--frozen", "--group", "dev"],
        cwd=package_dir,
        env=env,
    )


def assert_import_points_here(package_dir: Path) -> Path:
    env = _test_env(package_dir)
    out = subprocess.check_output(
        ["uv", "run", "python", "-c", "import langgraph.pregel as p; print(p.__file__)"],
        cwd=package_dir,
        env=env,
        text=True,
    ).strip()
    path = Path(out).resolve()
    expected = (package_dir / "langgraph" / "pregel").resolve()
    if expected not in path.parents and path.parent != expected:
        # __file__ is .../langgraph/pregel/__init__.py
        if not str(path).startswith(str(package_dir.resolve())):
            raise RuntimeError(
                f"Editable import escaped workspace.\n  got: {path}\n  expected under: {package_dir}"
            )
    return path


def _test_env(package_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "VIRTUAL_ENV"):
        env.pop(k, None)
    # Critical: filter via env var, not argv quoting
    env["PYTEST_ADDOPTS"] = '-k "not postgres"'
    return env


def overlay_tests(repo: Path, fix_sha: str, workspace_root: Path, test_files: tuple[str, ...]) -> None:
    for rel in test_files:
        data = git_show_file(repo, fix_sha, rel)
        dest = workspace_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def apply_gold_src(repo: Path, fix_sha: str, workspace_root: Path, src_files: tuple[str, ...]) -> None:
    for rel in src_files:
        data = git_show_file(repo, fix_sha, rel)
        dest = workspace_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def build_parent_workspace(
    task: Task,
    repo: Path | None = None,
    dest: Path | None = None,
    *,
    sync_venv: bool = True,
) -> Workspace:
    repo = repo or default_repo()
    fix_sha = resolve_commit(repo, task.short_commit)
    parent_sha = resolve_parent(repo, fix_sha)

    owns = dest is None
    if dest is None:
        dest = Path(tempfile.mkdtemp(prefix=f"memeval-{task.id}-parent-"))
    else:
        dest.mkdir(parents=True, exist_ok=True)

    extract_tree(repo, parent_sha, dest)
    overlay_tests(repo, fix_sha, dest, task.test_files)

    package_dir = dest / "libs" / "langgraph"
    if sync_venv:
        ensure_package_venv(package_dir)
        assert_import_points_here(package_dir)

    return Workspace(
        root=dest,
        package_dir=package_dir,
        parent_sha=parent_sha,
        fix_sha=fix_sha,
        owns_dir=owns,
    )


def apply_gold_to_workspace(ws: Workspace, task: Task, repo: Path | None = None) -> None:
    repo = repo or default_repo()
    apply_gold_src(repo, ws.fix_sha, ws.root, task.src_files)
    assert_import_points_here(ws.package_dir)
