"""Generate the judge-facing HTML explainer from disk + the Instrument design system.

Every figure, SHA, test count, token count, and status is read from files.
Raw colors live only in docs/design/tokens.css (inlined at generation time).
"""

from __future__ import annotations

import html
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

# Only these exit_reasons count as a scored attempt (agent actually ran to a
# harness outcome). Plumbing failures (missing_api_key, auth_error, exception, …)
# keep the page in the honest "0 scored attempts" empty state.
_SCORED_EXIT = frozenset(
    {
        "ok",
        "completed",
        "end_turn",
        "max_turns",
        "timeout",
        "test_tampering",
        "suspicious_empty_diff",
    }
)

_RAW_COLOR = re.compile(
    r"#[0-9A-Fa-f]{3,8}\b|\brgba?\(|\bhsla?\(",
    re.IGNORECASE,
)


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def design_dir() -> Path:
    return project_root() / "docs" / "design"


def results_dir() -> Path:
    return project_root() / "results"


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_catalog() -> list[dict[str, Any]]:
    data = _read_json(project_root() / "tasks" / "catalog.json")
    tasks = data.get("tasks") if isinstance(data, dict) else data
    if not isinstance(tasks, list) or not tasks:
        raise SystemExit("tasks/catalog.json has no tasks[]")
    return tasks


def load_facts() -> dict[str, Any]:
    return _read_json(project_root() / "docs" / "facts.json")


def load_integrity_spec() -> list[dict[str, Any]]:
    data = _read_json(project_root() / "docs" / "integrity.json")
    checks = data.get("checks") if isinstance(data, dict) else data
    if not isinstance(checks, list):
        raise SystemExit("docs/integrity.json has no checks[]")
    return checks


def langgraph_repo() -> Path:
    from memeval.workspace import default_repo

    return default_repo()


def verify_catalog_commits(tasks: list[dict[str, Any]], repo: Path) -> dict[str, str]:
    """Resolve each catalog short sha; fail loudly if one does not resolve."""
    resolved: dict[str, str] = {}
    for task in tasks:
        short = str(task.get("commit") or "")
        tid = str(task.get("id") or "?")
        if not short:
            raise SystemExit(f"catalog task {tid} missing commit")
        try:
            full = subprocess.check_output(
                ["git", "rev-parse", "--verify", short],
                cwd=repo,
                text=True,
                stderr=subprocess.STDOUT,
            ).strip()
        except subprocess.CalledProcessError as e:
            raise SystemExit(
                f"catalog commit {short!r} for {tid} does not resolve in {repo}: {e.output}"
            ) from e
        resolved[tid] = full
    return resolved


def newest_validate() -> dict[str, Any] | None:
    paths = sorted(results_dir().glob("validate-*.json"))
    if not paths:
        return None
    return _read_json(paths[-1])


def load_attempts() -> list[dict[str, Any]]:
    """Attempt result JSON files (exclude validate- and transcripts)."""
    out: list[dict[str, Any]] = []
    d = results_dir()
    if not d.exists():
        return out
    for path in sorted(d.glob("*.json")):
        name = path.name
        if name.startswith("validate-"):
            continue
        if name.endswith(".transcript.json"):
            continue
        try:
            out.append(_read_json(path))
        except json.JSONDecodeError:
            continue
    return out


def is_scored_attempt(run: dict[str, Any]) -> bool:
    return str(run.get("exit_reason") or "") in _SCORED_EXIT


def derive_integrity(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Probe the codebase for each check; fall back to wait when a probe fails."""
    rows: list[dict[str, Any]] = []
    root = project_root()
    for check in checks:
        probe = check.get("probe") or {}
        rel = str(probe.get("path") or "")
        needles = list(probe.get("must_contain") or [])
        state = "wait"
        path = root / rel if rel else None
        if path and path.is_file():
            text = path.read_text(encoding="utf-8")
            if needles and all(n in text for n in needles):
                state = "ok"
            elif not needles:
                state = "ok"
            else:
                state = "bad"
        rows.append(
            {
                "id": check.get("id"),
                "label": check.get("label"),
                "detail": check.get("detail"),
                "state": state,
            }
        )
    return rows


def _fmt_num(value: Any, *, digits: int | None = None) -> str:
    if value is None or value == "":
        return "—"
    try:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int):
            return str(value)
        f = float(value)
        if digits is not None:
            return f"{f:.{digits}f}"
        if f.is_integer():
            return str(int(f))
        return str(f)
    except (TypeError, ValueError):
        return str(value)


def _chip(state: str) -> str:
    cls = {"ok": "ok", "wait": "wait", "bad": "bad"}.get(state, "")
    label = state if state else "—"
    return f'<span class="chip {cls}">{_esc(label)}</span>'


def design_check(paths: list[Path] | None = None) -> list[str]:
    """Return violation messages for raw colors outside tokens.css."""
    root = project_root()
    targets = paths or [
        design_dir() / "system.css",
        design_dir() / "preview.html",
        root / "memeval" / "htmlreport.py",
    ]
    violations: list[str] = []
    for path in targets:
        if not path.is_file():
            violations.append(f"missing: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if _RAW_COLOR.search(line):
                violations.append(f"{path.relative_to(root)}:{i}: raw color: {line.strip()}")
    return violations


def _inline_design_css() -> str:
    tokens = (design_dir() / "tokens.css").read_text(encoding="utf-8")
    system = (design_dir() / "system.css").read_text(encoding="utf-8")
    return tokens + "\n" + system


def _gate_block(validate: dict[str, Any] | None) -> str:
    if not validate:
        return '<div class="empty">No validate-*.json on disk yet — gate numbers unavailable.</div>'
    parent = validate.get("parent_sha") or "—"
    fix = validate.get("fix_sha") or "—"
    parent_failed = validate.get("parent_failed")
    gold_passed = validate.get("gold_passed")
    regressions = (validate.get("comparison") or {}).get("regressions")
    if regressions is None:
        regressions = []
    # Timestamp from filename if present in run metadata workspace — use file mtime via path? 
    # Prefer fields on the newest validate file name from disk.
    stamp = "—"
    paths = sorted(results_dir().glob("validate-*.json"))
    if paths:
        # validate-T1-20261016T200726Z.json
        name = paths[-1].stem
        parts = name.split("-")
        if len(parts) >= 3:
            stamp = parts[-1]
    return f"""
    <div class="panel" style="margin-top: var(--space-4)">
      <span class="label">Newest validate gate</span>
      <div class="ledger" style="border: none; background: transparent">
        <div class="row">
          <div class="row-label">Parent SHA<span class="row-detail code">{_esc(parent)}</span></div>
          <span class="chip">{_esc(_fmt_num(parent_failed))} failed</span>
        </div>
        <div class="row">
          <div class="row-label">Fix SHA<span class="row-detail code">{_esc(fix)}</span></div>
          <span class="chip">{_esc(_fmt_num(gold_passed))} passed</span>
        </div>
        <div class="row">
          <div class="row-label">Regressions<span class="row-detail">{_esc(stamp)}</span></div>
          <span class="chip tabular">{_esc(len(regressions))}</span>
        </div>
      </div>
      <p class="prose" style="margin-top: var(--space-3); margin-bottom: 0">
        This control runs before any agent does.
      </p>
    </div>
    """


def _status_block(attempts: list[dict[str, Any]]) -> str:
    scored = [a for a in attempts if is_scored_attempt(a)]
    if not scored:
        reasons = Counter(str(a.get("exit_reason") or "unknown") for a in attempts)
        if not attempts:
            return '<div class="empty">0 scored attempts — no attempt JSON under results/ yet.</div>'
        items = "".join(
            f'<div class="row"><div class="row-label code">{_esc(reason)}</div>'
            f'<span class="chip tabular">{_esc(count)}</span></div>'
            for reason, count in sorted(reasons.items())
        )
        return f"""
        <div class="empty">0 scored attempts — recorded failure reasons:</div>
        <div class="ledger" style="margin-top: var(--space-3)">{items}</div>
        """

    # Scorecard: one row per condition (latest scored per condition)
    by_cond: dict[str, dict[str, Any]] = {}
    for a in scored:
        cond = str(a.get("condition") or "—")
        by_cond[cond] = a  # sorted load → last wins as newest-ish; keep last in list order
    # Prefer chronological by run_id suffix if present
    scored_sorted = sorted(scored, key=lambda r: str(r.get("run_id") or ""))
    by_cond = {}
    for a in scored_sorted:
        by_cond[str(a.get("condition") or "—")] = a

    rows = []
    for cond, a in by_cond.items():
        regs = a.get("regressions") or []
        rows.append(
            "<tr>"
            f"<td><code>{_esc(cond)}</code></td>"
            f"<td class=\"tabular\">{_esc(a.get('resolved'))}</td>"
            f"<td class=\"tabular\">{_esc(_fmt_num(a.get('partial'), digits=2))}</td>"
            f"<td class=\"tabular\">{_esc(len(regs))}</td>"
            f"<td class=\"tabular\">{_esc(_fmt_num(a.get('turns')))}</td>"
            f"<td class=\"tabular\">{_esc(_fmt_num(a.get('input_tokens')))} / {_esc(_fmt_num(a.get('output_tokens')))}</td>"
            f"<td class=\"tabular\">{_esc(_fmt_num(a.get('cost_usd'), digits=4))}</td>"
            f"<td class=\"tabular\">{_esc(_fmt_num(a.get('wall_time_s'), digits=1))}</td>"
            f"<td><code>{_esc(a.get('exit_reason'))}</code></td>"
            "</tr>"
        )
    return f"""
    <div class="data-table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>Condition</th>
            <th>Resolved</th>
            <th>Partial</th>
            <th>Regressions</th>
            <th>Turns</th>
            <th>Tokens in/out</th>
            <th>Cost</th>
            <th>Wall s</th>
            <th>Exit</th>
          </tr>
        </thead>
        <tbody>
          {"".join(rows)}
        </tbody>
      </table>
    </div>
    """


def render_html(
    *,
    catalog: list[dict[str, Any]] | None = None,
    facts: dict[str, Any] | None = None,
    integrity_spec: list[dict[str, Any]] | None = None,
    validate: dict[str, Any] | None = None,
    attempts: list[dict[str, Any]] | None = None,
    resolved_commits: dict[str, str] | None = None,
) -> str:
    catalog = catalog if catalog is not None else load_catalog()
    facts = facts if facts is not None else load_facts()
    integrity_spec = integrity_spec if integrity_spec is not None else load_integrity_spec()
    validate = validate if validate is not None else newest_validate()
    attempts = attempts if attempts is not None else load_attempts()
    if resolved_commits is None:
        resolved_commits = verify_catalog_commits(catalog, langgraph_repo())

    css = _inline_design_css()
    hero = facts.get("hero") or {}
    coverage = facts.get("agents_md_coverage") or {}
    conditions = facts.get("conditions") or []
    pipeline = facts.get("pipeline") or []
    pairs = facts.get("transfer_pairs") or []
    limits = facts.get("limits") or []
    premise = facts.get("premise") or []

    n_tasks = len(catalog)
    n_conditions = len(conditions)
    llm_judges = facts.get("llm_judges")
    # "test-backed fixes mined" = catalog size (each entry is a mined fix)
    fixes_mined = n_tasks

    tiles = f"""
    <div class="tile-grid">
      <div class="tile"><p class="label">Tasks validated</p><p class="figure tabular">{_esc(n_tasks)}</p></div>
      <div class="tile"><p class="label">Test-backed fixes</p><p class="figure tabular">{_esc(fixes_mined)}</p></div>
      <div class="tile"><p class="label">Conditions</p><p class="figure tabular">{_esc(n_conditions)}</p></div>
      <div class="tile"><p class="label">LLM judges</p><p class="figure accent tabular">{_esc(llm_judges)}</p></div>
    </div>
    """

    steps_html = []
    for i, step in enumerate(pipeline, 1):
        gate = " gate" if step.get("gate") else ""
        steps_html.append(
            f'<div class="step{gate}">'
            f'<span class="stage-num">{_esc(f"{i:02d}")}</span>'
            f'<p class="step-title">{_esc(step.get("title"))}</p>'
            f'<p class="step-body">{_esc(step.get("body"))}</p>'
            f"</div>"
        )

    cond_html = []
    for c in conditions:
        focus = " focus" if c.get("focus") else ""
        cond_html.append(
            f'<div class="cond{focus}">'
            f'<p class="cond-name"><code>{_esc(c.get("title"))}</code></p>'
            f'<p class="cond-body">{_esc(c.get("body"))}</p>'
            f"</div>"
        )

    have = coverage.get("have")
    of = coverage.get("of")
    lc_lines = coverage.get("langchain_lines")
    coverage_note = coverage.get("note") or ""

    task_rows = []
    for t in catalog:
        tid = str(t.get("id"))
        full = resolved_commits.get(tid, "")
        short = t.get("commit")
        cls = "warmup" if t.get("warmup") else ""
        arrow = f"{_fmt_num(t.get('parent_fail'))} fail → {_fmt_num(t.get('fixed_pass'))} pass"
        task_rows.append(
            f'<tr class="{cls}">'
            f"<td>{_esc(tid)}</td>"
            f"<td><code title=\"{_esc(full)}\">{_esc(short)}</code></td>"
            f"<td>{_esc(t.get('date'))}</td>"
            f"<td>{_esc(t.get('fix'))}</td>"
            f"<td class=\"tabular\">{_esc(_fmt_num(t.get('src_lines')))}</td>"
            f"<td class=\"tabular\">{_esc(arrow)}</td>"
            "</tr>"
        )

    pair_rows = []
    for p in pairs:
        commits = p.get("commits") or []
        commit_html = " → ".join(f"<code>{_esc(c)}</code>" for c in commits) if commits else "—"
        pair_rows.append(
            f'<div class="row">'
            f'<div class="row-label"><strong>{_esc(p.get("label"))}</strong> — {_esc(p.get("summary"))}'
            f'<span class="row-detail">{commit_html}</span></div>'
            f'</div>'
        )

    integrity_rows = derive_integrity(integrity_spec)
    ledger = "".join(
        f'<div class="row"><div class="row-label">{_esc(r["label"])}'
        f'<span class="row-detail">{_esc(r["detail"])}</span></div>'
        f'{_chip(str(r["state"]))}</div>'
        for r in integrity_rows
    )

    limits_html = "".join(
        f'<div class="limit"><strong>{_esc(L.get("title"))}</strong>{_esc(L.get("body"))}</div>'
        for L in limits
    )

    premise_html = "".join(f'<p class="prose">{_esc(p)}</p>' for p in premise)
    supporting = "".join(
        f'<p class="prose">{_esc(s)}</p>' for s in (hero.get("supporting") or [])
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_esc(facts.get("product"))} — {_esc(facts.get("harness"))}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500&family=Martian+Mono:wght@400;500&display=swap" rel="stylesheet" />
  <style>
{css}
  </style>
</head>
<body>
  <div class="page">
    <header class="masthead">
      <div class="wordmark">{_esc(facts.get("product"))}</div>
      <div class="meta-row">
        <span>Target · {_esc(facts.get("target_repo"))}</span>
        <span>Scorer · {_esc(facts.get("scorer"))}</span>
        <span>LLM judges · {_esc(llm_judges)}</span>
      </div>
    </header>

    <section style="margin-top: var(--space-6)">
      <h1 class="display-hero">{_esc(hero.get("question"))}</h1>
      <div style="margin-top: var(--space-4)">{supporting}</div>
      <div style="margin-top: var(--space-5)">{tiles}</div>
    </section>

    <section class="section">
      <h2 class="section-title">Premise</h2>
      {premise_html}
    </section>

    <section class="section">
      <h2 class="section-title">Scoring pipeline</h2>
      <div class="flow">{"".join(steps_html)}</div>
      {_gate_block(validate)}
    </section>

    <section class="section">
      <h2 class="section-title">Conditions</h2>
      <div class="cond-grid">{"".join(cond_html)}</div>
      <p class="prose" style="margin-top: var(--space-4)">
        {_esc(have)} of {_esc(of)} candidate repos already ship a hand-written AGENTS.md
        (LangChain’s is {_esc(lc_lines)} lines), so “no context” is a straw man — the question
        is whether learned memory beats what maintainers wrote by hand.
        {_esc(coverage_note)}
      </p>
    </section>

    <section class="section">
      <h2 class="section-title">Task pool</h2>
      <div class="data-table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>Id</th>
              <th>Commit</th>
              <th>Date</th>
              <th>Fix</th>
              <th>Src lines</th>
              <th>Parent → fixed</th>
            </tr>
          </thead>
          <tbody>
            {"".join(task_rows)}
          </tbody>
        </table>
      </div>
    </section>

    <section class="section">
      <h2 class="section-title">Transfer pairs</h2>
      <div class="ledger">{"".join(pair_rows)}</div>
    </section>

    <section class="section">
      <h2 class="section-title">Integrity ledger</h2>
      <div class="ledger">{ledger}</div>
    </section>

    <section class="section">
      <h2 class="section-title">Status</h2>
      {_status_block(attempts)}
    </section>

    <section class="section">
      <h2 class="section-title">Limits</h2>
      {limits_html}
    </section>
  </div>
</body>
</html>
"""


def write_html(path: Path) -> Path:
    violations = design_check()
    if violations:
        raise SystemExit("design-check failed:\n" + "\n".join(violations))
    catalog = load_catalog()
    resolved = verify_catalog_commits(catalog, langgraph_repo())
    html_text = render_html(catalog=catalog, resolved_commits=resolved)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")
    return path
