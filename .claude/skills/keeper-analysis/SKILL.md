---
name: keeper-analysis
description: >
  Get rosters and rankings ready for keeper decisions, then hand off to the live,
  interactive keeper board at /keepers-board (or /league/<id>/keepers for a
  non-Yahoo league) where keepers are actually selected. Computes keeper
  eligibility (round restrictions, consecutive-season caps) and builds the
  post-keepers draft board behind that page. Triggers on "keeper analysis",
  "keeper board", "keeper insight", "best keepers", "forecast keepers",
  "/keeper-analysis", or when user prepares for keeper decisions.
---

Automate keeper *data prep* without asking, then point at the live page —
keeper selection itself now happens by clicking player cards on
`/keepers-board` (Yahoo) or `/league/<id>/keepers` (any other league), not by
reading a CSV or editing a config file. The web app auto-picks a starting
point per team from current rankings, but every click is a real choice: 0, 1,
or up to the league's keeper cap can be kept per team, and every keeper-
eligible player on the roster shows as a card, not just the algorithm's top
picks — a manager may have a real reason to keep someone who doesn't rank
near the top of the board.

## Standard workflow (2026+)

```bash
# 1. Update rosters (interactive, copy-paste from Yahoo)
python3 -m app parse-rosters

# 2. Refresh rankings to the current standard (free FFC ADP + Sleeper tail +
#    QB historical adjustment, all in one run)
python3 -m app refresh-free-rankings

# 3. Open the live board and pick keepers there:
#    - Yahoo: /keepers-board
#    - Any other league: /league/<league-slug>/keepers
```

Requires: `data/raw/rosters/yahoo_league_rosters.json` (or a synced Sleeper/ESPN
snapshot for non-Yahoo leagues), `data/raw/rankings/yahoo_rankings.json`.

That's the whole loop — no CSV export, no config file to hand-edit for a
one-off manager intent. On `/keepers-board` / `/league/<id>/keepers`:
- Every keeper-eligible player on a team's roster shows as a card, ranked.
- The algorithm's own top picks (by rank, then positional scarcity, then
  keeper-years-remaining as tiebreaks — see "Keeper rules" below) start
  checked; nothing is forced.
- Click a card to toggle it kept/not-kept. Cards never disappear or reorder
  as you click — only the "kept" border changes. Trying to check more than
  the league's keeper cap gets rejected with a clear message, not silently
  ignored or auto-swapped.
- Keeper impact by position and the post-keeper draft board update live on
  the same page, no reload.
- Picks persist per-league in the `keeper_marks` DB table (not a file to
  re-run/re-import) — reload the page any time to see the current state.

## Legacy / secondary paths (still available, not the primary flow)

**CSV export + versioned snapshots** — for comparing how recommendations
shift across roster/ranking snapshots over time (e.g. before vs. after a
trade), not for making the actual keeper picks:

```bash
python3 -m app keepers-board-export
```
Writes timestamped `keepers_YYYYMMDD_HHMM.csv` + `draft_board_YYYYMMDD_HHMM.csv`
to `data/processed/keeper_exports/`. `/keepers-board` has a version dropdown
that auto-discovers these and can load a past export instead of the live
computed board — useful for "what would keepers have looked like a week
ago," not for today's decision.

**`data/config/keeper_preferences.json`** — legacy Yahoo-only, hand-edited
per-team override file, superseded by clicking cards on the live page for
anyone with an account. Still read as a fallback on `/keepers-board` /
`/` / the draft-order board if a team has no live click-based marks yet.

**Read-only CLI views** — still useful for a quick terminal check without
opening the browser:
```bash
python3 -m app keeper-insight     # your roster only, full eligibility breakdown
python3 -m app best-keepers       # your roster only, top N + alternates
python3 -m app forecast-keepers   # all teams, predicted picks
python3 -m app keepers-board      # all teams, prints top-2 picks + writes
                                   # data/processed/rankings_post_keepers.csv
```
None of these write anything the live page reads — they're informational
only, not part of the selection loop.

## Keeper rules (Frank Gore Memorial League)

- **2 keeper slots per team** (this league; other leagues set their own cap
  in `/league/<id>/settings`), no draft-round cost
- Players drafted in **round 1–2 are ineligible** to be kept
- **2-year consecutive-season cap** — can't keep same player 3+ years in a row
- Keepers occupy last 2 rounds of draft (round 14–15 in 15-round draft)
- Keeper picks based on **current value** (ranking + positional scarcity), not
  "rounds saved" — full detail in `data/config/league_rules.json`

## Pre-analysis checklist

Before pointing the user at the live board:
1. Rosters current: `python3 -m app parse-rosters` (Yahoo) or a recent sync
   for Sleeper/ESPN leagues.
2. Rankings current: `python3 -m app refresh-free-rankings` (also runs daily
   via the background scheduler, so this is often already fresh).
3. League format set: `/league/<id>/settings` has the right keeper slot
   count, ineligible rounds, and consecutive-cap for that league.

## Auto-triggers

Run this prep, then point at the live board, when user:
- Mentions keeper deadline approaching
- Asks which players to keep
- Wants to compare keeper value across league
- Prepares for draft strategy
- Requests "keeper board", "keeper insight", or "keeper analysis"
- Updates rosters and wants to see updated recommendations
- Wants to compare snapshots before/after roster changes (use the CSV export
  path above for this one, not the live page)
