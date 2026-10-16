"""Terminal + markdown reports (milestone 4)."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table


def results_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "results"


def load_run_jsons() -> list[dict]:
    out: list[dict] = []
    d = results_dir()
    if not d.exists():
        return out
    for p in sorted(d.glob("*.json")):
        if p.name.startswith("validate-"):
            continue
        try:
            out.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            continue
    return out


def print_report(runs: list[dict] | None = None) -> None:
    runs = runs if runs is not None else load_run_jsons()
    console = Console()
    if not runs:
        console.print("[yellow]No run results yet.[/yellow]")
        return
    table = Table(title="MemEval results")
    for col in (
        "run_id",
        "task",
        "condition",
        "resolved",
        "partial",
        "regressions",
        "tokens",
        "cost",
        "seconds",
        "turns",
    ):
        table.add_column(col)
    for r in runs:
        table.add_row(
            str(r.get("run_id", "")),
            str(r.get("task_id", "")),
            str(r.get("condition", "")),
            str(r.get("resolved", "")),
            f"{r.get('partial', '')}",
            str(len(r.get("regressions", []) or [])),
            str(r.get("tokens", "")),
            str(r.get("cost_usd", "")),
            str(r.get("wall_time_s", "")),
            str(r.get("turns", "")),
        )
    console.print(table)


def write_markdown(path: Path | None = None) -> Path:
    runs = load_run_jsons()
    path = path or (results_dir() / "report.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# MemEval report",
        "",
        "Demo / small-n results. Not statistically significant unless noted.",
        "",
        "| run_id | task | condition | resolved | partial | regressions | tokens | cost | seconds | turns |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in runs:
        lines.append(
            "| {run_id} | {task} | {cond} | {res} | {partial} | {reg} | {tok} | {cost} | {sec} | {turns} |".format(
                run_id=r.get("run_id", ""),
                task=r.get("task_id", ""),
                cond=r.get("condition", ""),
                res=r.get("resolved", ""),
                partial=r.get("partial", ""),
                reg=len(r.get("regressions", []) or []),
                tok=r.get("tokens", ""),
                cost=r.get("cost_usd", ""),
                sec=r.get("wall_time_s", ""),
                turns=r.get("turns", ""),
            )
        )
    path.write_text("\n".join(lines) + "\n")
    return path
