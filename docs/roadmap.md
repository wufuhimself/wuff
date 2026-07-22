# wuff Roadmap

Feature development plan for the Yahoo Fantasy Football Analyst.

## Near-term: Extend existing capability

### 1. Enhance gems & busts analysis
**Status:** Partially built (`app/gem_finder.py`, `/gems` page exists)

- Surface `rank_vs_adp` trends by position — which positions have the most
  overrated/underrated players historically
- Flag repeat-gem players across seasons — find the sleepers who keep
  outperforming their draft slot
- Add a "confidence" metric per gem/bust based on how many seasons they've
  repeated the pattern

**Why:** `feature_table.csv` (draft_history + nfl_stats + rankings + rosters)
is already built and joined; this just adds SQL/pandas aggregations over
existing columns.

### 2. Trade analyzer
**Status:** Zero — but high reuse potential**

- Given two rosters, compute the VOR (value-over-replacement) impact of a
  proposed trade for both sides
- Reuse `app/strategy.py` logic (positional scarcity for this league's
  roster shape) + `rankings_combined.json` to score each side
- Web form: paste two rosters, pick a player from each, see if the trade
  makes sense for both teams

**Why:** The scoring logic exists; just needs a rosters-as-input view and a
trade math layer on top.

### 3. Draft-day live mode
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

### 2. Waiver-wire gem alerts
**Status:** Zero — extends gem_finder.py to weekly data**

- Flag current-season breakouts using weekly `nfl_stats` — find the waiver
  pickups who are outperforming their ADP in-season
- Extend `gem_finder.py` (which works on full-season stats) to sliding
  windows: gems through week 4, week 8, week 12, etc.
- Useful for mid-season trade deadlines or when people are scanning
  available players

**Why:** `nfl_stats/weekly/` data already exists; gem_finder logic (rank vs
points) just needs a window parameter.

### 3. ML model: predicting breakout seasons
**Status:** Zero — but feature_table.csv is ready-made**

- Train a simple model on feature_table.csv to predict `fantasy_points`
  given draft inputs: `draft_round`, `adp`, `rank_consensus`, `age`,
  `position`, etc.
- Use it to rank next season's prospects: who looks good on paper vs who's
  undervalued by ADP / rankings
- Start simple (scikit-learn linear regression or a decision tree), not
  deep learning

**Why:** Feature table is already built — this is a pure modeling task.
Start with correlation analysis before committing to a model.

## Stretch: Beyond the app

### Discord/Slack bot
**Status:** Zero**

- Use `app/gem_finder.py` + `app/strategy.py` as a library (not views)
- Post weekly breakout alerts and keeper reminders to the league Discord
- Useful for async engagement — teammates don't have to remember to visit
  the site

**Why:** Reuses all existing analysis logic; just adds a different UI layer
(Discord messages instead of web pages).

## Prioritization

1. **Highest signal:** Manager report card + gems by position — these tell
   stories about your league and are immediately fun to read.
2. **Next:** Trade analyzer and waiver gem alerts — high utility during
   active season play.
3. **Model:** Once feature table is well-validated (report card + gems prove
   the data is clean), build the ML piece.
4. **Discord:** Nice-to-have after the web app is hosted and stable.
