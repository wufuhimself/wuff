---
name: keeper-analysis
description: >
  Run keeper board and keeper insight analysis. Computes keeper eligibility (round restrictions,
  consecutive-season caps), selects best keepers per team, and builds post-keepers draft board.
  Triggers on "keeper analysis", "keeper board", "keeper insight", "best keepers", "forecast keepers",
  "/keeper-analysis", or when user prepares for keeper decisions.
---

Automate keeper analysis without asking.

## Autonomous keeper-picking agent

New workflow for multiple roster snapshots with before/after comparison:

```bash
# 1. Update rosters (interactive)
python3 -m app parse-rosters

# 2. Export keeper recommendations + draft board (timestamped CSVs)
python3 -m app keepers-board-export

# 3. View in Flask web app at /keepers-board
#    Auto-discovers all keepers_*.csv and draft_board_*.csv files
#    Compare snapshots to see how recommendations shifted as rosters changed
```

Requires: `data/raw/rosters/yahoo_league_rosters.json`, `data/raw/rankings/rankings_combined.json` (or yahoo_rankings.json fallback)

Outputs (per export run):
- `keepers_YYYYMMDD_HHMM.csv` — per-team keeper picks (2 keepers) + alternates (top 3), one row per player
- `draft_board_YYYYMMDD_HHMM.csv` — remaining players ranked for draft after keepers removed, full 15-round draft order
- Files auto-discovered by Flask `/keepers-board` route for browsing and comparison

## Commands by workflow

### Full league keeper board (legacy)

```bash
python3 -m app keepers-board
```

Requires: `data/raw/rosters/yahoo_league_rosters.json`, `data/raw/rankings/yahoo_rankings.json`

Outputs:
- Prints top-2 keeper picks for all 12 teams
- Writes `data/processed/rankings_post_keepers.csv` (full draft board with keepers removed)

### Your roster only

```bash
python3 -m app keeper-insight
python3 -m app best-keepers
```

**keeper-insight:** Full per-player breakdown, eligibility status, why each is/isn't eligible, 
years remaining before hitting the 2-season cap.

**best-keepers:** Just the top N picks + alternates (default top 2).

Both read: `data/raw/rosters/yahoo_roster.json`, `data/raw/rankings/yahoo_rankings.json`

### Forecast opponent keepers

```bash
python3 -m app forecast-keepers
```

Predicts likely keeper selections across all teams before the deadline. Shows top available
players after forecasted keepers are removed.

## Customization

```bash
python3 -m app keepers-board --teams 10 --count 2
python3 -m app best-keepers --teams 12 --count 3
```

- `--teams`: number of teams in league (default 12)
- `--count`: keeper slots per team (default 2)
- `--input` / `--output`: custom snapshot/CSV paths

## Keeper rules (Frank Gore Memorial League)

- **2 keeper slots per team**, no draft-round cost
- Players drafted in **round 1–2 are ineligible** to be kept
- **2-year consecutive-season cap** — can't keep same player 3+ years in a row
- Keepers occupy last 2 rounds of draft (round 14–15 in 15-round draft)
- Keeper picks based on **current value** (ranking + positional scarcity), not "rounds saved"

## Pre-analysis checklist

Before running keeper analysis:
1. Ensure rankings are combined: `python3 -m app combine-rankings` (creates `data/raw/rankings/rankings_combined.json`)
2. Ensure rosters are current:
   - Use `python3 -m app parse-rosters` to upload updated rosters (interactive, copy-paste from Yahoo)
   - Or: `python3 -m app scrape-league-rosters` to web-scrape all team rosters
3. Ensure league format is set: check with `python3 -m app show-league-format` (already configured: 12-team, 1 QB, 2 RB, 3 WR, 1 TE, 1 SUPERFLEX, 1 DEF)

## Auto-triggers

Run keeper analysis when user:
- Mentions keeper deadline approaching
- Asks which players to keep
- Wants to compare keeper value across league
- Prepares for draft strategy
- Requests "keeper board", "keeper insight", or "keeper analysis"
- Updates rosters and wants to see updated recommendations
- Wants to compare snapshots before/after roster changes
