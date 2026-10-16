"""Attempt executors: local temp dir (default) and optional Daytona sandboxes.

Daytona lets us run two conditions in isolated VMs in parallel and compare.
Local path is required to work first; Daytona is opt-in via --executor daytona.
"""

from __future__ import annotations

import io
import os
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from memeval.tasks import Task
from memeval.workspace import build_parent_workspace


@dataclass
class PreparedWorkspace:
    root: Path
    package_dir: Path
    parent_sha: str
    fix_sha: str
    cleanup: Callable[[], None]


class Executor(Protocol):
    def prepare(self, task: Task, *, condition: str) -> PreparedWorkspace: ...


def _apply_condition(root: Path, condition: str) -> None:
    """Mutate workspace for the memory condition."""
    agents = root / "AGENTS.md"
    if condition == "none":
        if agents.exists():
            agents.unlink()
        # Also strip nested AGENTS.md if present
        for p in root.rglob("AGENTS.md"):
            try:
                p.unlink()
            except OSError:
                pass
    # agents_md and agents_md+memory keep AGENTS.md; memory injected via prompt


class LocalExecutor:
    """Fresh git-archive tree + own venv on this machine."""

    def prepare(self, task: Task, *, condition: str) -> PreparedWorkspace:
        ws = build_parent_workspace(task, sync_venv=True)
        _apply_condition(ws.root, condition)

        def cleanup() -> None:
            ws.cleanup()

        return PreparedWorkspace(
            root=ws.root,
            package_dir=ws.package_dir,
            parent_sha=ws.parent_sha,
            fix_sha=ws.fix_sha,
            cleanup=cleanup,
        )


class DaytonaExecutor:
    """Two (or more) isolated Daytona sandboxes — one per condition/attempt.

    Requires DAYTONA_API_KEY. Uploads a parent snapshot tarball, syncs venv remotely,
    runs the agent inside the sandbox. Use this to A/B conditions without shared disk.
    """

    def __init__(self) -> None:
        if not os.environ.get("DAYTONA_API_KEY"):
            raise RuntimeError("DAYTONA_API_KEY is not set")
        from daytona import Daytona

        self._daytona = Daytona()
        self._sandboxes: list = []

    def prepare(self, task: Task, *, condition: str) -> PreparedWorkspace:
        """Create a sandbox and materialize the parent+tests tree inside it.

        For v1 we still prepare a *local* archive workspace for scoring simplicity,
        then upload it into the sandbox for the agent to edit. After the agent runs,
        we download the patched tree back for local pytest scoring (same scorer).
        """
        from daytona import CreateSandboxFromSnapshotParams

        # Local materialization (source of truth for scoring / diff)
        local = build_parent_workspace(task, sync_venv=True)
        _apply_condition(local.root, condition)

        sandbox = self._daytona.create(
            CreateSandboxFromSnapshotParams(
                language="python",
                env_vars={
                    "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
                    "PYTEST_ADDOPTS": '-k "not postgres"',
                },
                auto_stop_interval=30,
                labels={"memeval": "1", "task": task.id, "condition": condition},
                network_block_all=False,  # agent needs Anthropic API; tighten later
            ),
            timeout=120,
        )
        self._sandboxes.append(sandbox)

        # Upload workspace as tar
        tar_bytes = _tar_directory(local.root)
        remote_tar = "/tmp/memeval-ws.tar"
        remote_root = "/home/daytona/memeval-ws"
        sandbox.fs.upload_file(tar_bytes, remote_tar)
        sandbox.process.exec(f"mkdir -p {remote_root} && tar -xf {remote_tar} -C {remote_root}")

        def cleanup() -> None:
            try:
                self._daytona.delete(sandbox)
            except Exception:  # noqa: BLE001
                pass
            local.cleanup()

        # Expose remote path via env file for the Daytona agent adapter
        marker = local.root / ".memeval_daytona.json"
        import json

        marker.write_text(
            json.dumps(
                {
                    "sandbox_id": sandbox.id,
                    "remote_root": remote_root,
                }
            )
        )

        return PreparedWorkspace(
            root=local.root,
            package_dir=local.package_dir,
            parent_sha=local.parent_sha,
            fix_sha=local.fix_sha,
            cleanup=cleanup,
        )

    def download_remote_tree(self, prepared: PreparedWorkspace) -> None:
        """Pull agent edits from Daytona sandbox back onto local root."""
        import json

        marker = prepared.root / ".memeval_daytona.json"
        if not marker.exists():
            return
        meta = json.loads(marker.read_text())
        sandbox = self._daytona.get(meta["sandbox_id"])
        remote_root = meta["remote_root"]
        sandbox.process.exec(f"tar -cf /tmp/memeval-out.tar -C {remote_root} .")
        data = sandbox.fs.download_file("/tmp/memeval-out.tar")
        # Clear local (keep .venv) then extract over sources
        _extract_tar_bytes(data, prepared.root, skip_prefixes=(".venv/",))


def _tar_directory(root: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in root.rglob("*"):
            if ".venv" in path.parts:
                continue
            if path.is_file():
                tar.add(path, arcname=str(path.relative_to(root)))
    return buf.getvalue()


def _extract_tar_bytes(data: bytes, dest: Path, skip_prefixes: tuple[str, ...] = ()) -> None:
    bio = io.BytesIO(data)
    with tarfile.open(fileobj=bio, mode="r:*") as tar:
        for member in tar.getmembers():
            if any(member.name.startswith(p) or f"/{p}" in member.name for p in skip_prefixes):
                continue
            tar.extract(member, path=dest)


def get_executor(name: str) -> Executor:
    if name == "local":
        return LocalExecutor()
    if name == "daytona":
        return DaytonaExecutor()
    raise SystemExit(f"Unknown executor {name!r}; use local|daytona")
