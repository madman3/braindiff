"""Capture PNGs of the real explainer page and validate terminal card.

Requires: docs/index.html generated, docs/screenshots/validate-T1.log present.
Overwrites images in place. Compresses with oxipng when available.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SHOTS = DOCS / "screenshots"
INDEX = DOCS / "index.html"
TERM_HTML = SHOTS / "validate-terminal.html"
TERM_LOG = SHOTS / "validate-T1.log"

DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}
SCALE = 2
MAX_BYTES = 600 * 1024

ELEMENT_SHOTS = (
    ("01-hero.png", "#hero"),
    ("02-pipeline.png", "#scoring"),
    ("03-conditions.png", "#conditions"),
    ("04-task-pool.png", "#task-pool"),
    ("05-integrity.png", "#integrity"),
    ("06-status.png", "#status"),
)


async def _prep(page) -> None:
    await page.wait_for_function("document.fonts.ready")
    await page.add_style_tag(
        content="* { animation: none !important; transition: none !important; }"
    )
    # Give webfonts a beat after fonts.ready (some browsers resolve early).
    await page.wait_for_timeout(200)


def _file_url(path: Path) -> str:
    return path.resolve().as_uri()


def _compress(path: Path) -> None:
    oxipng = shutil.which("oxipng")
    if oxipng:
        subprocess.run(
            [oxipng, "-o", "4", "--strip", "safe", str(path)],
            check=False,
            capture_output=True,
        )
    size = path.stat().st_size
    if size > MAX_BYTES:
        print(f"warning: {path.name} is {size} bytes (>{MAX_BYTES})", file=sys.stderr)


async def _clip_selector(page, selector: str, dest: Path) -> None:
    loc = page.locator(selector)
    await loc.wait_for(state="visible")
    await loc.scroll_into_view_if_needed()
    await page.wait_for_timeout(100)
    # Element screenshot keeps the section in frame even when below the fold.
    await loc.screenshot(path=str(dest), type="png")
    _compress(dest)
    print(f"Wrote {dest} ({dest.stat().st_size} bytes)")


async def capture() -> None:
    if not INDEX.is_file():
        raise SystemExit(f"missing {INDEX}; run: uv run memeval report --html docs/index.html")
    if not TERM_LOG.is_file():
        raise SystemExit(
            f"missing {TERM_LOG}; run: "
            "uv run memeval validate T1 2>&1 | tail -n 12 | tee docs/screenshots/validate-T1.log"
        )

    # Build terminal card HTML from the log.
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "termcard.py"), "--log", str(TERM_LOG), "--out", str(TERM_HTML)],
        check=True,
    )

    SHOTS.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            viewport=DESKTOP,
            device_scale_factor=SCALE,
            color_scheme="dark",
        )
        page = await context.new_page()
        await page.goto(_file_url(INDEX), wait_until="networkidle")
        await _prep(page)

        for name, selector in ELEMENT_SHOTS:
            await _clip_selector(page, selector, SHOTS / name)

        # Full page desktop
        full = SHOTS / "full-page.png"
        await page.screenshot(path=str(full), type="png", full_page=True)
        _compress(full)
        if full.stat().st_size > MAX_BYTES:
            # Cap width at 1200 rather than lowering scale factor.
            await page.set_viewport_size({"width": 1200, "height": 900})
            await page.goto(_file_url(INDEX), wait_until="networkidle")
            await _prep(page)
            await page.screenshot(path=str(full), type="png", full_page=True)
            _compress(full)
        print(f"Wrote {full} ({full.stat().st_size} bytes)")

        # Mobile
        await context.close()
        mobile_ctx = await browser.new_context(
            viewport=MOBILE,
            device_scale_factor=SCALE,
            color_scheme="dark",
            is_mobile=True,
            has_touch=True,
        )
        mpage = await mobile_ctx.new_page()
        await mpage.goto(_file_url(INDEX), wait_until="networkidle")
        await _prep(mpage)
        mobile = SHOTS / "mobile.png"
        await mpage.screenshot(path=str(mobile), type="png", full_page=True)
        _compress(mobile)
        print(f"Wrote {mobile} ({mobile.stat().st_size} bytes)")
        await mobile_ctx.close()

        # Terminal card
        tctx = await browser.new_context(
            viewport={"width": 1000, "height": 600},
            device_scale_factor=SCALE,
            color_scheme="dark",
        )
        tpage = await tctx.new_page()
        await tpage.goto(_file_url(TERM_HTML), wait_until="networkidle")
        await _prep(tpage)
        await _clip_selector(tpage, "#termcard", SHOTS / "validate-terminal.png")
        await tctx.close()
        await browser.close()

    # Size report
    total = 0
    for png in sorted(SHOTS.glob("*.png")):
        total += png.stat().st_size
        over = " OVER" if png.stat().st_size > MAX_BYTES else ""
        print(f"  {png.name}: {png.stat().st_size}{over}")
    print(f"total png bytes: {total}")
    if total > 4 * 1024 * 1024:
        raise SystemExit(f"screenshots total {total} exceeds 4 MB")


def main() -> None:
    asyncio.run(capture())


if __name__ == "__main__":
    main()
