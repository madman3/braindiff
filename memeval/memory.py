"""Lesson store (JSONL). Extraction/injection wired in milestone 3."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def memory_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "memory"


@dataclass
class Lesson:
    id: str
    source_task: str
    condition: str
    text: str
    created_at: str
    manual: bool = False


def lessons_path() -> Path:
    return memory_dir() / "lessons.jsonl"


def manual_path() -> Path:
    return memory_dir() / "manual.jsonl"


def append_lesson(lesson: Lesson, path: Path | None = None) -> None:
    p = path or lessons_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(lesson), ensure_ascii=False) + "\n")


def load_lessons(*paths: Path) -> list[Lesson]:
    out: list[Lesson] = []
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            out.append(Lesson(**data))
    return out


def lessons_for_tasks(task_ids: Iterable[str], include_manual: bool = True) -> list[Lesson]:
    wanted = {t.upper() for t in task_ids}
    paths = [lessons_path()]
    if include_manual:
        paths.append(manual_path())
    lessons = load_lessons(*paths)
    return [L for L in lessons if L.source_task.upper() in wanted or L.manual]


def format_lessons_block(lessons: list[Lesson]) -> tuple[str, list[str]]:
    if not lessons:
        return "", []
    lines = ["## Lessons from previous work in this repo", ""]
    ids: list[str] = []
    for L in lessons:
        ids.append(L.id)
        lines.append(f"- ({L.id}) {L.text}")
    lines.append("")
    return "\n".join(lines), ids


def new_lesson_id(source_task: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{source_task.lower()}-{stamp}"
