"""Render validate-T1.log as a framed terminal card HTML (Instrument tokens)."""

from __future__ import annotations

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOKENS = ROOT / "docs" / "design" / "tokens.css"
DEFAULT_LOG = ROOT / "docs" / "screenshots" / "validate-T1.log"
DEFAULT_OUT = ROOT / "docs" / "screenshots" / "validate-terminal.html"


def render(log_text: str, tokens_css: str) -> str:
    # Escape HTML specials; keep the log byte-for-byte in content after unescaping.
    escaped = (
        log_text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    # Colorize the OK line only — do not reword.
    lines = escaped.splitlines()
    colored: list[str] = []
    for line in lines:
        if line.strip().startswith("OK"):
            colored.append(f'<span class="ok">{line}</span>')
        elif line.strip().startswith("FAIL"):
            colored.append(f'<span class="bad">{line}</span>')
        else:
            colored.append(line)
    body = "\n".join(colored)
    if log_text.endswith("\n"):
        body += "\n"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>validate T1</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet" />
  <style>
{tokens_css}
    html, body {{
      margin: 0;
      background: var(--ground);
      color: var(--ink);
      font-family: var(--font-mono);
    }}
    .card {{
      margin: 24px;
      background: var(--panel);
      border: var(--hairline, 1px) solid var(--line);
      border-radius: var(--radius, 2px);
      overflow: hidden;
      max-width: 920px;
    }}
    .prompt {{
      padding: 12px 16px;
      border-bottom: var(--hairline, 1px) solid var(--line);
      color: var(--ink-3);
      font-size: 12.5px;
      letter-spacing: 0.02em;
    }}
    .prompt .cmd {{
      color: var(--ink);
    }}
    pre {{
      margin: 0;
      padding: 16px;
      font-family: var(--font-mono);
      font-size: 12.5px;
      line-height: 1.55;
      white-space: pre-wrap;
      word-break: break-word;
      color: var(--ink-2);
      font-variant-numeric: tabular-nums;
    }}
    .ok {{ color: var(--acid); font-weight: 500; }}
    .bad {{ color: var(--rose); font-weight: 500; }}
  </style>
</head>
<body>
  <div class="card" id="termcard">
    <div class="prompt">$ <span class="cmd">uv run memeval validate T1</span></div>
    <pre id="log">{body}</pre>
  </div>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if not args.log.is_file():
        raise SystemExit(f"missing log: {args.log}")
    tokens = TOKENS.read_text(encoding="utf-8")
    log_text = args.log.read_text(encoding="utf-8")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(log_text, tokens), encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
