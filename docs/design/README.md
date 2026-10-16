# Instrument

A small design system for BrainDiff / MemEval judge-facing surfaces: dark instrument panel, mono type, one acid accent, hairline grid.

Direction only from a certain industrial-ops aesthetic. Not a copy — no borrowed logo, wordmark, copy, illustrations, or licensed fonts.

## Files

| File | Role |
| --- | --- |
| `tokens.css` | The only place raw colors may appear. Type, space, hairline, grid. |
| `system.css` | Primitives built exclusively from `var(--token)`. |
| `preview.html` | Styleguide: every token swatch and every primitive variant. |
| `README.md` | These rules. |

The HTML report inlines `tokens.css` + `system.css` at generation time. Edit the system here; regenerate the page.

## Rules

1. **One accent.** Acid green (`--acid`) carries emphasis. Amber and rose are *state only* — pending and broken — never decoration. A page with three decorative colors has none.

2. **Hairlines, not cards.** Structure comes from 1px `--line` dividers and shared edges. No shadows, no radii above `--radius` (2px), no glow, no gradients.

3. **Numbering encodes sequence.** Stage numerals (`.stage-num`, `--font-display`) appear on the pipeline because it is a real ordered process. They never appear on panels, which are not a sequence.

4. **Figures are tabular.** `font-variant-numeric: tabular-nums` anywhere digits stack (`.tabular`, `.tile .figure`, `.data-table`).

5. **Labels are mono, uppercase, tracked** (~`0.15em` via `--track-label`). Prose is sans (`.prose`) and never tracked.

6. **Raw hex lives in `tokens.css` and nowhere else.** `uv run memeval design-check` enforces this on `system.css`, `preview.html`, and the page renderer.

## Type

| Role | Token | Use |
| --- | --- | --- |
| Display | `--font-display` (Martian Mono) | Hero headline and stage numerals only. Weights 400 / 500. |
| Mono | `--font-mono` (IBM Plex Mono) | Labels, figures, SHAs, code. The default. |
| Sans | `--font-sans` (IBM Plex Sans) | Prose paragraphs only. |

Never weight 600 or 700.

## Primitives

| Class | Purpose |
| --- | --- |
| `.panel` | Section container with a left eyebrow rail. |
| `.tile` | Single metric: label + figure. Optional `.figure.accent`. |
| `.flow` / `.step` | Horizontal pipeline. `.step.gate` accents the control gate. |
| `.cond` | Condition card. `.cond.focus` marks the real baseline. |
| `.ledger` / `.row` | Integrity / status list with a trailing chip. |
| `.chip` | State: `.ok` / `.wait` / `.bad`. |
| `.data-table` | Tabular task / scorecard data (wrap in `.data-table-wrap`). |
| `.limit` | Constraint callout under Limits. |

## Hygiene

```bash
uv run memeval design-check
```

Fails if `#hex`, `rgb(`, or `hsl(` appears outside `tokens.css`.
