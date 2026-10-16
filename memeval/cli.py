"""CLI entrypoints."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

# Load gitignored .env if present (ANTHROPIC_*, DAYTONA_*, MEMEVAL_*).
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass

app = typer.Typer(add_completion=False, no_args_is_help=True, help="MemEval: memory-transfer eval harness")
console = Console()


@app.command()
def validate(
    task_id: str = typer.Argument(..., help="Task id, e.g. T1"),
    keep: bool = typer.Option(False, "--keep", help="Keep temp workspace on disk"),
) -> None:
    """Sanity-check harness: parent+tests fail, gold source makes tests pass."""
    from memeval.runner import validate_task

    result = validate_task(task_id, keep_workspace=keep)
    console.print()
    console.print(f"[bold]validate {result.task_id}[/bold]")
    console.print(f"  parent: {result.parent_sha[:12]}")
    console.print(f"  fix:    {result.fix_sha[:12]}")
    console.print(f"  import: {result.import_file}")
    console.print(
        f"  parent → {result.parent_failed} failed, {result.parent_passed} passed  ({result.parent_summary})"
    )
    console.print(
        f"  gold   → {result.gold_failed} failed, {result.gold_passed} passed  ({result.gold_summary})"
    )
    if result.ok:
        console.print("[green]OK[/green]  parent fails and gold fix passes")
        raise typer.Exit(0)
    console.print("[red]FAIL[/red]  expected parent failures then gold resolve")
    raise typer.Exit(1)


@app.command("run")
def run_cmd(
    task_id: str = typer.Argument(...),
    condition: str = typer.Option("none", "--condition"),
    lessons_from: list[str] | None = typer.Option(None, "--lessons-from"),
    executor: str = typer.Option("local", "--executor", help="local | daytona"),
    seed: int = typer.Option(0, "--seed"),
    keep: bool = typer.Option(False, "--keep"),
    max_turns: int = typer.Option(40, "--max-turns"),
    timeout_s: int = typer.Option(600, "--timeout-s"),
    model: str = typer.Option("claude-sonnet-4-5", "--model"),
) -> None:
    """Run one agent attempt and score with task tests."""
    from memeval.agent import Budget
    from memeval.runner import run_attempt

    payload = run_attempt(
        task_id,
        condition=condition,
        lessons_from=lessons_from or [],
        executor_name=executor,
        seed=seed,
        budget=Budget(max_turns=max_turns, timeout_s=timeout_s, model=model),
        keep_workspace=keep,
    )
    console.print()
    console.print(f"[bold]run {payload['run_id']}[/bold]")
    console.print(f"  condition: {payload['condition']}  executor: {payload['executor']}")
    console.print(f"  exit: {payload['exit_reason']}  error: {payload.get('error')}")
    console.print(
        f"  resolved={payload['resolved']}  partial={payload['partial']:.2f}  "
        f"regressions={len(payload['regressions'])}"
    )
    console.print(
        f"  turns={payload['turns']}  tokens={payload['tokens']}  "
        f"cost={payload['cost_usd']}  seconds={payload['wall_time_s']:.1f}"
    )
    console.print(
        f"  diff: files={payload.get('diff_files_changed', 0)} "
        f"+{payload.get('diff_added_lines', 0)}/-{payload.get('diff_removed_lines', 0)}"
    )
    if payload.get("injected_lesson_ids"):
        console.print(f"  injected lessons: {payload['injected_lesson_ids']}")
    if payload.get("test_files_tampered"):
        console.print(
            "[bold red]TEST TAMPERING DETECTED[/bold red] "
            f"overlaid task tests were modified: {payload['test_files_tampered']}. "
            "Marking resolved=false (exit_reason=test_tampering)."
        )
    if payload["exit_reason"] == "missing_api_key":
        raise typer.Exit(3)
    raise typer.Exit(0 if payload["resolved"] else 1)


@app.command()
def experiment(
    name: str = typer.Argument("pair-a"),
    executor: str = typer.Option("local", "--executor"),
) -> None:
    """Run Pair A train+test conditions (milestone 4)."""
    if name != "pair-a":
        console.print(f"[yellow]Only pair-a is wired; got {name}[/yellow]")
        raise typer.Exit(2)
    console.print(
        "[yellow]experiment pair-a[/yellow] needs a successful T1 train run first "
        "(milestone 3). Use: memeval run T1 --condition none"
    )
    raise typer.Exit(2)


@app.command()
def report(
    html: Path | None = typer.Option(
        None,
        "--html",
        help="Write the Instrument HTML explainer to this path (e.g. docs/index.html)",
        dir_okay=False,
        writable=True,
    ),
) -> None:
    """Terminal + markdown report; optional self-contained HTML explainer."""
    from memeval.report import print_report, write_markdown

    print_report()
    path = write_markdown()
    console.print(f"Wrote {path}")
    if html is not None:
        from memeval.htmlreport import write_html

        out = write_html(html)
        console.print(f"Wrote {out}")


@app.command("design-check")
def design_check_cmd() -> None:
    """Fail if raw colors appear outside docs/design/tokens.css."""
    from memeval.htmlreport import design_check

    violations = design_check()
    if violations:
        console.print("[red]design-check failed[/red]")
        for v in violations:
            console.print(f"  {v}")
        raise typer.Exit(1)
    console.print("[green]design-check OK[/green]  raw colors confined to tokens.css")


if __name__ == "__main__":
    app()
