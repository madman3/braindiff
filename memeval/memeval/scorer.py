"""Run task tests and parse per-test outcomes."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class TestOutcome:
    nodeid: str
    outcome: str  # passed | failed | skipped | error


@dataclass
class ScoreResult:
    resolved: bool
    passed: int
    failed: int
    skipped: int
    errors: int
    total_collected: int
    outcomes: list[TestOutcome] = field(default_factory=list)
    failing_nodeids: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    returncode: int = 0
    raw_summary: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _pytest_report_path() -> Path:
    fd, name = tempfile.mkstemp(prefix="memeval-pytest-", suffix=".json")
    os.close(fd)
    return Path(name)


def run_task_tests(package_dir: Path, test_files: tuple[str, ...]) -> ScoreResult:
    """Run only the task's test files. Uses PYTEST_ADDOPTS for postgres filter."""
    env = os.environ.copy()
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        env.pop(k, None)
    env["PYTEST_ADDOPTS"] = '-k "not postgres"'

    report = _pytest_report_path()
    # Paths relative to package_dir (libs/langgraph)
    rel_tests: list[str] = []
    for tf in test_files:
        # tf is monorepo-relative like libs/langgraph/tests/foo.py
        prefix = "libs/langgraph/"
        if tf.startswith(prefix):
            rel_tests.append(tf[len(prefix) :])
        else:
            rel_tests.append(tf)

    cmd = [
        "uv",
        "run",
        "pytest",
        *rel_tests,
        "-v",
        "--tb=short",
        f"--report-log={report}",
    ]
    # Fallback if report-log plugin missing: parse -v output. Prefer JSON report via pytest-json?
    # langgraph may not have pytest-reportlog. Use --junitxml instead (stdlib in pytest).
    junit = report.with_suffix(".xml")
    cmd = [
        "uv",
        "run",
        "pytest",
        *rel_tests,
        "-v",
        "--tb=line",
        f"--junitxml={junit}",
    ]

    import time

    t0 = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=package_dir,
        env=env,
        text=True,
        capture_output=True,
    )
    duration = time.perf_counter() - t0
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    combined = stdout + "\n" + stderr

    outcomes = _parse_junit(junit) if junit.exists() else _parse_verbose(combined)
    passed = sum(1 for o in outcomes if o.outcome == "passed")
    failed = sum(1 for o in outcomes if o.outcome == "failed")
    skipped = sum(1 for o in outcomes if o.outcome == "skipped")
    errors = sum(1 for o in outcomes if o.outcome == "error")
    failing = [o.nodeid for o in outcomes if o.outcome in ("failed", "error")]
    resolved = proc.returncode == 0 and failed == 0 and errors == 0 and passed > 0

    # cleanup temp
    report.unlink(missing_ok=True)
    junit.unlink(missing_ok=True)

    return ScoreResult(
        resolved=resolved,
        passed=passed,
        failed=failed,
        skipped=skipped,
        errors=errors,
        total_collected=len(outcomes),
        outcomes=outcomes,
        failing_nodeids=failing,
        duration_s=duration,
        returncode=proc.returncode,
        raw_summary=_last_summary_line(combined),
    )


def _last_summary_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        if "passed" in line or "failed" in line or "error" in line:
            if "===" in line or "passed" in line or "failed" in line:
                return line.strip()
    return text[-500:]


def _parse_junit(path: Path) -> list[TestOutcome]:
    import xml.etree.ElementTree as ET

    tree = ET.parse(path)
    root = tree.getroot()
    # pytest junit may wrap in <testsuites>
    cases = root.findall(".//testcase")
    out: list[TestOutcome] = []
    for case in cases:
        classname = case.attrib.get("classname", "")
        name = case.attrib.get("name", "")
        nodeid = f"{classname}::{name}" if classname else name
        if case.find("failure") is not None:
            outcome = "failed"
        elif case.find("error") is not None:
            outcome = "error"
        elif case.find("skipped") is not None:
            outcome = "skipped"
        else:
            outcome = "passed"
        out.append(TestOutcome(nodeid=nodeid, outcome=outcome))
    return out


def _parse_verbose(text: str) -> list[TestOutcome]:
    outcomes: list[TestOutcome] = []
    for line in text.splitlines():
        if " PASSED" in line or line.endswith("PASSED"):
            node = line.split(" PASSED", 1)[0].strip().split(" ", 1)[-1]
            if "::" in line:
                node = line.strip().split()[0]
                outcomes.append(TestOutcome(nodeid=node, outcome="passed"))
        elif " FAILED" in line:
            if "::" in line:
                node = line.strip().split()[0]
                outcomes.append(TestOutcome(nodeid=node, outcome="failed"))
        elif " ERROR" in line and "::" in line:
            node = line.strip().split()[0]
            outcomes.append(TestOutcome(nodeid=node, outcome="error"))
    return outcomes


def compare_scores(parent: ScoreResult, after: ScoreResult) -> dict:
    """Compute partial / regressions relative to parent failing set."""
    parent_fail = set(parent.failing_nodeids)
    parent_pass = {o.nodeid for o in parent.outcomes if o.outcome == "passed"}
    after_pass = {o.nodeid for o in after.outcomes if o.outcome == "passed"}
    after_fail = set(after.failing_nodeids)

    if parent_fail:
        newly_fixed = parent_fail & after_pass
        partial = len(newly_fixed) / len(parent_fail)
    else:
        partial = 1.0 if after.resolved else 0.0

    regressions = sorted(parent_pass & after_fail)
    return {
        "partial": partial,
        "fixed_from_parent_failures": sorted(parent_fail & after_pass),
        "still_failing": sorted(parent_fail & after_fail),
        "regressions": regressions,
        "parent_failed": len(parent_fail),
        "after_passed": after.passed,
        "after_failed": after.failed,
    }
