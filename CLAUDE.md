# wuff — Yahoo Fantasy Football Helper

Before doing any keeper, draft-ranking, or roster-value analysis, read
`data/config/league_rules.json` first. It has the league's keeper rules
(no keeper cost, round 1/2 ineligible, 2-consecutive-season cap), a map of
which data file holds what, and a map of which app module owns
which piece of logic. Reading it avoids re-deriving rules by grepping
through `app/strategy.py` and `app/draft_history.py` from
scratch each session.

Key files:
- `data/config/league_rules.json` — rules + file/code map (read this first)
- `data/raw/rosters/yahoo_league_rosters.json` — current league rosters (updated via parse-rosters)
- `data/raw/rankings/rankings_combined.json` — combined multi-source rankings (created via combine-rankings)
- `data/raw/draft_history/{year}.json` — past draft results, one file per season
- `data/raw/draft_picks/{year}.json` — pick ownership by round for a draft year
- `data/processed/keeper_exports/` — timestamped keeper recommendation CSVs (source for keeper board)
- `data/processed/keeper_board.html` — interactive keeper board viewer (load in browser)
- `scripts/sync_keeper_board.js` — auto-syncs keeper exports to keeper board HTML
- `app/strategy.py` — keeper eligibility/selection logic

## Keeper-picking agent (autonomous recommendations)

Autonomous keeper agent recommends best 2 keepers for each team, ranks remaining draft board, tracks changes across roster snapshots.

**Workflow:**
1. Update rosters: `python -m app parse-rosters`
   - Paste raw Yahoo Fantasy text (copy-pasted from browser)
   - Parser normalizes names, looks up NFL teams from rankings
   - Shows preview, asks to confirm save to `yahoo_league_rosters.json`
2. Export keeper recommendations: `python -m app keepers-board-export`
   - Reads current rosters + combined rankings
   - Applies league rules (round 1/2 ineligible, 2-consecutive-season cap)
   - Scores eligible players: rank-first, VOR/keeper-years-remaining as tiebreaks
   - Outputs two CSVs (timestamped for snapshots):
     - `keepers_YYYYMMDD_HHMM.csv` — per-team picks + alternates
     - `draft_board_YYYYMMDD_HHMM.csv` — remaining board, ranked for draft prep
3. Compare snapshots: review timestamped CSVs to see how recommendations shifted as rosters changed

**Keeper scoring logic:**
- Primary: overall ranking (market consensus)
- Tiebreak 1: value over replacement rounds (positional scarcity for this league's roster shape)
- Tiebreak 2: keeper years remaining (players with multi-year runway preferred)
- Never: rank-based QB bypass (non-rushing QBs stay lower than WR2/WR3 tier even if ranked higher)

### Keeper board versioning (CSV-driven)

Keeper recommendations are exported as timestamped, method-tagged CSVs in `data/processed/keeper_exports/`. The keeper board web page loads these CSVs and renders interactive versions—allows comparing recommendations across ranking methods and roster snapshots.

**CSV format:** `keepers_YYYYMMDD_METHOD.csv`
- **Columns:** Team, Keeper1, K1_Pos, K1_ADP, Keeper2, K2_Pos, K2_ADP, Method, Locked
- **Method:** scoring method used (e.g., `rank-first`, `adp`, `vor-only`)
- **Locked:** boolean; true if keeper was manually locked by user (overrides algorithm)
- **Example:** `keepers_20260722_adp.csv` — ADP-based picks from July 22, 2026

**Workflow:**
1. Generate keeper export: script runs keeper selection algorithm, outputs CSV to `keeper_exports/`
2. Board page auto-discovers all CSVs in `keeper_exports/`, lists them by (date, method)
3. User selects version to view; board renders table + shows alternates from rosters
4. Side-by-side compare: pick two versions to see how recommendations changed

**Why versioning matters:**
- Rankings update (new sources added, old ones refreshed) → ADP shifts → keepers change
- Rosters shift (trades, roster moves) → eligibility changes → selections change
- Multiple methods (rank-first vs ADP vs VOR-only) → different picks for same team
- Historical snapshots let you forecast vs actual keeper decisions after draft

**Workflow: Export → View**

1. Generate keeper export via Python script or CLI → outputs CSV to `keeper_exports/`
   - Filename must follow pattern: `keepers_YYYYMMDD_METHOD.csv` or `keepers_YYYYMMDD_HHMM.csv`
   - Example: `keepers_20260722_adp.csv`, `keepers_20260722_rank-first.csv`, `keepers_20260722_0128.csv`
2. Visit `/keepers-board` route in Flask web app
3. Dropdown auto-discovers all keeper CSVs in `keeper_exports/`, sorted newest-first
4. Select version to view keeper board + compare across methods/dates

**Integration details (in `app/web.py`):**
- `list_keeper_exports()` — scans keeper_exports/, parses filename for date/method
- `load_keeper_export(filename)` — loads CSV, groups keepers by team
- `/keepers-board` route — queries `?version=` param, loads selected export, passes to template
- Template shows version dropdown + keeper table + forecast + draft board

No manual sync needed; Flask auto-discovers CSVs on each page load.

## Multi-source rankings + ML feature pipeline (2026)

New modules for combining rankings and building ML-ready feature tables:

### Rankings ingestion (`app/rankings_manager.py`)

Combines multiple ranking sources (Yahoo, ESPN, FantasyPros, etc) in any format (JSON/CSV/PDF) into a single normalized file.

**Workflow:**
1. Save each ranking source to `data/raw/rankings/{source}_rankings.{json|csv|pdf}`
   - **CSV:** auto-detects `playerName`/`player`, `ranking`/`rank`, `position`, `team` columns
   - **JSON:** list of `{playerId, playerName, position, team, ranking, source}` objects
   - **PDF:** extracts from tables or text (handles multi-column layouts, format: `N. (POS#) PlayerName, TEAM`)
2. Run `python -m app combine-rankings`
   - Loads all sources (JSON/CSV/PDF), normalizes player IDs, averages ranks
   - Outputs `data/raw/rankings/rankings_combined.json`
3. Lookup: `from app.rankings_manager import get_player_rank`

**Key functions:**
- `load_all_rankings()` — read all CSV/JSON files in `data/raw/rankings/`
- `normalize_rankings()` — standardize format (player ID, rank scale, position)
- `combine_rankings()` — group by player, average across sources
- `save_combined_rankings()` — persist to JSON
- `get_player_rank()` — lookup player's consensus rank

### Feature table builder (`app/feature_table.py`)

Builds ML-ready feature table by joining draft history + NFL stats + rankings + rosters.
One row per player-season.

**Workflow:**
1. Ensure combined rankings exist (run `combine-rankings` first)
2. Run `python -m app build-features --seasons 2022 2023 2024 2025`
   - Joins: draft_history, nfl_stats (seasonal), rosters (age/bye), rankings
   - Outputs `data/processed/feature_table.csv`
3. Load into Pandas for analysis: `pd.read_csv('data/processed/feature_table.csv')`

**Feature columns:**
- `player_id`, `player_name`, `season` — identifiers
- `draft_round`, `draft_pick`, `adp` — draft data
- `position`, `team`, `age`, `bye_week` — player profile
- `fantasy_points` — actual season performance
- `rank_consensus`, `rank_yahoo`, `rank_espn`, ...  — multiple ranking sources
- `source_count` — how many sources ranked this player
- `rank_vs_adp` — ranking minus draft position (higher = overrated)

**Key functions:**
- `load_adp_by_season()` — compute average draft position from draft_history
- `load_player_age_by_season()` — compute age from birth year in rosters
- `load_bye_weeks()` — extract bye week from rosters
- `load_season_stats_map()` — load fantasy points from nfl_stats
- `build_feature_table()` — join all sources
- `save_feature_table()` — write to CSV
- `build_and_save_feature_table()` — end-to-end
