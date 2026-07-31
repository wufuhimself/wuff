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

Keeper recommendations are exported as timestamped CSVs in `data/processed/keeper_exports/`. Export includes two files per snapshot: keeper picks + alternates, and post-keepers draft board. Flask auto-discovers all CSVs and allows comparing recommendations across roster snapshots.

**Keepers CSV format:** `keepers_YYYYMMDD_HHMM.csv`
- **Columns:** Team, PlayerName, Position, Ranking, Status, KeeperYearsRemaining, ValueOverReplacementRounds
- **Status:** `Keeper 1`, `Keeper 2`, `Alt 1`, `Alt 2`, or `Alt 3` (top 2 keepers + 3 alternates per team)
- **Example row:** Team=Wuf, PlayerName=Josh Allen, Position=QB, Ranking=2, Status=Keeper 1, KeeperYearsRemaining=2, ValueOverReplacementRounds=3

**Draft board CSV format:** `draft_board_YYYYMMDD_HHMM.csv`
- **Columns:** DraftOrder, PlayerName, Position, Ranking, PosRank, Team
- **DraftOrder:** pick number in full 15-round draft (1–180 for 12-team league)
- **Contains:** all players ranked after keepers are removed from the board

**Workflow:**
1. Generate keeper export: `python3 -m app.cli keepers-board-export` → outputs two CSVs to `keeper_exports/`
2. Visit `/keepers-board` route in Flask web app
3. Dropdown auto-discovers all `keepers_*.csv` files, sorted newest-first by timestamp
4. Select version to view keeper recommendations + draft board
5. Compare snapshots across dates/rankings to see how recommendations changed

**Why versioning matters:**
- Rankings update (new sources added, old ones refreshed) → keepers change
- Rosters shift (trades, roster moves) → eligibility changes → selections change
- Historical snapshots let you forecast which keepers teams will actually keep
- Multiple exports from same day (different ranking sources) show sensitivity to input data

**Integration details (in `app/web.py`):**
- `list_keeper_exports()` — scans keeper_exports/, parses filename for date/timestamp, returns sorted list
- `load_keeper_export(filename)` — loads keeper CSV, groups rows by team (multiple rows per team: keepers + alternates)
- `/keepers-board` route — queries `?version=` param, loads selected export, computes keeper impact analysis
- Template shows version dropdown + keeper table + draft board view

No manual sync needed; Flask auto-discovers CSVs on each page load.

## Multi-source rankings (2026)

Modules for combining rankings from multiple sources and importing ADP:

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

### ADP import (`app/adp_manager.py`)

Imports Average Draft Position (market consensus) from a CSV, used to enrich
keeper forecasts and mock draft picks with an ADP field.

```bash
python3 -m app import-adp data/raw/adp/fantasypros_adp.csv
```

Saves normalized ADP to `data/raw/adp/adp_combined.json`.
