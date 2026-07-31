# wuff Roadmap

Feature development plan for the Yahoo fantasy football assistant GM.

## Near-term: Extend existing capability

### 1. Trade analyzer
**Status:** Zero — but high reuse potential**

- Given two rosters, compute the VOR (value-over-replacement) impact of a
  proposed trade for both sides
- Reuse `app/strategy.py` logic (positional scarcity for this league's
  roster shape) + `rankings_combined.json` to score each side
- Web form: paste two rosters, pick a player from each, see if the trade
  makes sense for both teams

**Why:** The scoring logic exists; just needs a rosters-as-input view and a
trade math layer on top.

### 2. Draft-day live mode
**Status:** Zero — complex, pulls in multi-user considerations**

- A web page that updates the keeper board / draft order as picks come in
  during the live draft
- Real-time view: current round, next picks by team, remaining board ranked
  for the next pick

**Why:** Currently `/keepers-board` is a point-in-time snapshot, regenerated
via CLI. For live draft, teammates would need to see the same board
updating as picks happen, which implies multi-user read access (Phase 2 of
hosting plan).

**Scope note:** Deferred pending hosting decision (Option A vs Option B in
hosting plan); if read-only shared views, this is less relevant since only
you'd be running the ingestion.

## Mid-term: New capability, reusing existing data pipeline

### 1. Manager report card
**Status:** Skeleton exists (`app/draft_analysis.py` has draft_slot_vs_final_rank logic)**

- Grade each manager's keeper choices and draft picks against actual
  season outcomes
- Metrics per manager: "draft accuracy" (how many of your picks outperformed
  their ADP), "keeper ROI" (points scored by keepers vs keepers chosen by
  others), "trade success" (if trade data is captured)

**Why:** Already have draft_history + standings + nfl_stats joined in
draft_analysis.py; just need to reshape the aggregations to per-manager
and persist them as a viewable report.

## Stretch: Beyond the app

### Discord/Slack bot
**Status:** Zero**

- Use `app/strategy.py` as a library (not views)
- Post weekly keeper reminders to the league Discord
- Useful for async engagement — teammates don't have to remember to visit
  the site

**Why:** Reuses all existing analysis logic; just adds a different UI layer
(Discord messages instead of web pages).

## Prioritization

1. **Highest signal:** Manager report card — tells stories about your league
   and is immediately fun to read.
2. **Next:** Trade analyzer — high utility during active season play.
3. **Discord:** Nice-to-have after the web app is hosted and stable.
