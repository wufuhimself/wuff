---
name: visual-check
description: Render and visually inspect wuff pages after any template or CSS change. Use whenever app/templates/ (especially base.html) is edited, when a layout/alignment/overflow/wrapping issue is reported, or before claiming a visual change is done. Also carries this app's layout and copy conventions.
---

# Visual check

Template changes fail in ways no assertion catches: a baseline shift, a
wrapped cell, text clipped at a phone width. The check is **looking at the
rendered page**, not re-reading the CSS.

## When to run this

Automatically, without being asked:

- any edit under `app/templates/`, above all `base.html` (its inline
  `<style>` is the whole app's stylesheet — a change there cascades to every
  page)
- any report of something looking off, misaligned, cut off, or wrapping badly
- before saying a visual change is finished

## How

```bash
python3 scripts/shoot_pages.py                       # default pages, 1100 + 390
python3 scripts/shoot_pages.py /keepers-board        # specific paths
python3 scripts/shoot_pages.py --logged-out /        # the welcome page
python3 scripts/shoot_pages.py --full /standings     # whole page, not the viewport
```

It serves the real Flask app with a real logged-in session against a
throwaway copy of the local database, drives headless Chrome over CDP with
real device metrics, prints an `OVERFLOW` line for any page wider than its
viewport, and writes PNGs.

**Then read the PNGs with the Read tool and actually look at them.** The
overflow line catches one failure mode. Baseline drift, clipped text, a
cramped touch target and bad wrapping are only visible by looking.

Shoot the pages your change can reach. A `base.html` edit reaches all of
them, so shoot the default set plus anything specific you touched.

### Comparing before and after

For an alignment or spacing fix, render both and put them side by side —
identical markup, only the property under test differing. Reasoning about
`vertical-align` and baselines is unreliable; the screenshot is not.

## ⚠️ Do not use `--window-size` against a `file://` copy

Chrome headless ignores the viewport meta that way and lays out at a wider
viewport than requested: a 390px request rendered at 485px and made the
keeper-impact cards look clipped on mobile when the real page was fine. A
speculative `.card { min-width: 0 }` "fix" was committed against a bug that
did not exist, then reverted. `scripts/shoot_pages.py` exists because of
that false alarm — use it rather than hand-rolling a Chrome invocation.

If a screenshot shows something surprising, **confirm the viewport metrics
before changing CSS** (the script prints `doc=` and `viewport=`).

## Layout conventions in this app

- **All CSS is inline in `app/templates/base.html`.** No stylesheet, no
  framework. Style changes go there, not into a new file, and every template
  extends it.
- **Palette is CSS custom properties on `:root`** — `--purple`,
  `--purple-light`, `--pink`, `--green`, `--red`, `--text`, `--muted`,
  `--bg`, `--surface`, `--surface-alt`, `--border`. Use the tokens, never a
  raw hex. `--green` is reserved for success/positive values.
- **Type is fluid**: `clamp(min, vw, max)` rather than fixed sizes or
  per-breakpoint overrides. Match neighbouring elements' clamp shape.
- **640px is the primary breakpoint** (nav collapses to a hamburger). 700px
  and 1050px exist for specific grids.
- **`.card` already scrolls its own overflow** (`overflow-x: auto`), so a
  wide table inside one is handled — don't add another wrapper.
- **Grids reflow with** `repeat(auto-fit, minmax(min(100%, Npx), 1fr))`.
  The `min(100%, …)` matters: without it the track can't shrink below `Npx`
  and the grid overflows on a phone.
- **Long text needs an owner**: `overflow: hidden; text-overflow: ellipsis`
  plus a `title` attribute carrying the full string, which is what the roster
  and team-name cells do.
- **`.button-ghost` and `.button-secondary` are modifiers**, not standalone
  classes — always `class="button button-ghost"`.

### Baseline alignment

`.brand-row` and similar rows use `align-items: baseline`. **A flex or
inline-flex child replaces its text baseline with its own box baseline**, so
neighbouring text stops lining up. Keep such a child inline and nudge an
image with `vertical-align` instead. This is exactly how the header tagline
fell out of line with the WuFF wordmark.

Source whitespace between an inline image and adjacent text renders as a
real space, on top of any margin — close the tag against the text.

## Copy conventions

Plain and factual. No personas, no mascot voice — a wizard theme was built
and reverted (see `project_band_of_heroes_theme` memory).

- Landing-page cards run **one tight sentence, ~20-25 words**. Match the
  neighbours; the odd long one reads as clutter.
- Say what a thing does, not how it feels. Avoid idioms that don't survive a
  literal reading ("try a call before committing" read as gambling jargon).
- Don't explain internal constraints to a visitor. "Yahoo support is
  pending" beats a parenthetical about API approval status.
- Keeper wording is **gated** on a league having keeper slots — see
  CLAUDE.md's keeper-UI section before adding any.

## Verifying, not assuming

When adding a check for a visual bug, **break the thing it targets and watch
it fail**. A check written after the fix that was never seen red is a rubber
stamp. This applies to `scripts/check_*.py` too, and matching a rendered
element (`class="keeper-row"`) never a bare class name — `base.html`'s
inline `<style>` carries every class on every page.
