"""Token hygiene: raw colors only in docs/design/tokens.css."""

from __future__ import annotations

from pathlib import Path

from memeval.htmlreport import design_check, project_root


def test_design_check_passes_on_repo() -> None:
    assert design_check() == []


def test_design_check_catches_hex_in_system_css(tmp_path: Path) -> None:
    system = tmp_path / "system.css"
    system.write_text(".x { color: #abcdef; }\n", encoding="utf-8")
    preview = tmp_path / "preview.html"
    preview.write_text("<html></html>\n", encoding="utf-8")
    renderer = tmp_path / "htmlreport.py"
    renderer.write_text("# no colors\n", encoding="utf-8")
    violations = design_check([system, preview, renderer])
    assert any("system.css" in v and "#abcdef" in v for v in violations)
