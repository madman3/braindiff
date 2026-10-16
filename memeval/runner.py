"""Run validate / agent attempts and write results JSON."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from memeval.agent import AttemptResult, Budget, get_default_agent
from memeval.executors import get_executor
from memeval.memory import format_lessons_block, lessons_for_tasks
from memeval.scorer import compare_scores, run_task_tests
from memeval.tasks import ensure_task_paths, get_task
from memeval.workspace import default_repo, verify_test_files


def results_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "results"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


@dataclass
class ValidateResult:
    task_id: str
    parent_sha: str
    fix_sha: str
    parent_failed: int
    parent_passed: int
    gold_failed: int
    gold_passed: int
    ok: bool
    parent_summary: str
    gold_summary: str
    import_file: str
    workspace: str


def validate_task(task_id: str, keep_workspace: bool = False) -> ValidateResult:
    from memeval.workspace import (
        apply_gold_to_workspace,
        assert_import_points_here,
        build_parent_workspace,
    )

    repo = default_repo()
    task = ensure_task_paths(repo, get_task(task_id))

    ws = build_parent_workspace(task, repo=repo, sync_venv=True)
    try:
        import_file = str(assert_import_points_here(ws.package_dir))

        parent_score = run_task_tests(ws.package_dir, task.test_files)
        apply_gold_to_workspace(ws, task, repo=repo)
        gold_score = run_task_tests(ws.package_dir, task.test_files)

        ok = parent_score.failed > 0 and gold_score.resolved

        result = ValidateResult(
            task_id=task.id,
            parent_sha=ws.parent_sha,
            fix_sha=ws.fix_sha,
            parent_failed=parent_score.failed,
            parent_passed=parent_score.passed,
            gold_failed=gold_score.failed,
            gold_passed=gold_score.passed,
            ok=ok,
            parent_summary=parent_score.raw_summary,
            gold_summary=gold_score.raw_summary,
            import_file=import_file,
            workspace=str(ws.root),
        )

        out = results_dir()
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = out / f"validate-{task.id}-{stamp}.json"
        payload = {
            **asdict(result),
            "parent_score": parent_score.to_dict(),
            "gold_score": gold_score.to_dict(),
            "comparison": compare_scores(parent_score, gold_score),
            "test_files": list(task.test_files),
            "src_files": list(task.src_files),
        }
        path.write_text(json.dumps(payload, indent=2))
        print(f"Wrote {path}")
        return result
    finally:
        if not keep_workspace:
            ws.cleanup()


def _read_problem(task_id: str) -> str:
    path = project_root() / "tasks" / task_id.upper() / "problem.md"
    if not path.exists():
        raise SystemExit(f"Missing problem statement: {path}")
    return path.read_text(encoding="utf-8")


_DIFF_EXCLUDE = (
    "--exclude=__pycache__",
    "--exclude=*.pyc",
    "--exclude=.pytest_cache",
    "--exclude=.venv",
)


def diff_stats(diff_text: str) -> tuple[int, int, int]:
    """Return (files_changed, added_lines, removed_lines) for a unified diff."""
    files = 0
    added = 0
    removed = 0
    for line in diff_text.splitlines():
        if line.startswith("diff "):
            files += 1
            continue
        if line.startswith("+++ ") or line.startswith("--- "):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return files, added, removed


def run_attempt(
    task_id: str,
    *,
    condition: str = "none",
    lessons_from: list[str] | None = None,
    executor_name: str = "local",
    seed: int = 0,
    budget: Budget | None = None,
    keep_workspace: bool = False,
) -> dict:
    repo = default_repo()
    task = ensure_task_paths(repo, get_task(task_id))
    budget = budget or Budget()
    lessons_from = lessons_from or []

    if condition not in ("none", "agents_md", "agents_md+memory"):
        raise SystemExit(f"Unknown condition {condition!r}")

    extra = ""
    injected_ids: list[str] = []
    if condition == "agents_md+memory":
        if not lessons_from:
            raise SystemExit("agents_md+memory requires --lessons-from")
        lessons = lessons_for_tasks(lessons_from, include_manual=True)
        extra, injected_ids = format_lessons_block(lessons)

    executor = get_executor(executor_name)
    prepared = executor.prepare(task, condition=condition)

    # Snapshot pre-agent libs/langgraph tree for patch capture (whole package, not
    # just langgraph/langgraph — so test-file edits are visible too).
    pre_src = Path(prepared.root).parent / f"{prepared.root.name}-pre-src"
    if pre_src.exists():
        shutil.rmtree(pre_src)
    shutil.copytree(
        prepared.root / "libs" / "langgraph",
        pre_src,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".venv"),
    )

    parent_score = run_task_tests(prepared.package_dir, task.test_files)
    problem = _read_problem(task.id)

    agent = get_default_agent()
    attempt: AttemptResult = agent.solve(
        workspace=prepared.root,
        problem=problem,
        extra_context=extra,
        budget=budget,
    )

    # If Daytona, pull edits back (no-op for local)
    if executor_name == "daytona" and hasattr(executor, "download_remote_tree"):
        executor.download_remote_tree(prepared)

    # Detect test-file tampering before scoring
    tampered = verify_test_files(prepared.root, prepared.test_file_hashes)

    after_score = run_task_tests(prepared.package_dir, task.test_files)
    comparison = compare_scores(parent_score, after_score)

    proc = subprocess.run(
        [
            "diff",
            "-ruN",
            *_DIFF_EXCLUDE,
            str(pre_src),
            str(prepared.root / "libs" / "langgraph"),
        ],
        text=True,
        capture_output=True,
    )
    diff_text = proc.stdout or ""
    files_changed, added_lines, removed_lines = diff_stats(diff_text)

    exit_reason = attempt.exit_reason
    resolved = after_score.resolved
    if tampered:
        resolved = False
        exit_reason = "test_tampering"
    elif resolved and files_changed == 0 and (added_lines + removed_lines) == 0:
        # A "pass" with no source edits means the harness is lying.
        exit_reason = "suspicious_empty_diff"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{task.id}-{condition.replace('+', '_')}-s{seed}-{stamp}"
    out = results_dir()
    out.mkdir(parents=True, exist_ok=True)
    diff_path = out / f"{run_id}.diff"
    transcript_path = out / f"{run_id}.transcript.json"
    diff_path.write_text(diff_text)
    transcript_path.write_text(json.dumps(attempt.transcript, indent=2))

    payload = {
        "run_id": run_id,
        "task_id": task.id,
        "condition": condition,
        "seed": seed,
        "executor": executor_name,
        "parent_sha": prepared.parent_sha,
        "fix_sha": prepared.fix_sha,
        "budget": asdict(budget),
        "injected_lesson_ids": injected_ids,
        "injected_lessons": (
            [
                {"id": L.id, "text": L.text, "source_task": L.source_task}
                for L in lessons_for_tasks(lessons_from, include_manual=True)
            ]
            if condition == "agents_md+memory"
            else []
        ),
        "resolved": resolved,
        "partial": comparison["partial"],
        "regressions": comparison["regressions"],
        "parent_score": parent_score.to_dict(),
        "after_score": after_score.to_dict(),
        "comparison": comparison,
        "tokens": attempt.input_tokens + attempt.output_tokens,
        "input_tokens": attempt.input_tokens,
        "output_tokens": attempt.output_tokens,
        "cost_usd": attempt.cost_usd,
        "wall_time_s": attempt.wall_time_s,
        "turns": attempt.turns,
        "exit_reason": exit_reason,
        "error": attempt.error,
        "model": attempt.model,
        "diff_path": str(diff_path),
        "transcript_path": str(transcript_path),
        "diff_files_changed": files_changed,
        "diff_added_lines": added_lines,
        "diff_removed_lines": removed_lines,
        "workspace": str(prepared.root),
    }
    if tampered:
        payload["test_files_tampered"] = tampered
    result_path = out / f"{run_id}.json"
    result_path.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {result_path}")

    if not keep_workspace:
        prepared.cleanup()
        shutil.rmtree(pre_src, ignore_errors=True)

    return payload
