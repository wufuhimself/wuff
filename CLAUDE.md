# wuff — Fantasy Football Helper (Yahoo + Sleeper)

wuff is no longer Yahoo-only. The original league (Frank Gore Memorial
League, keeper/superflex rules below) is still the deepest-supported
league, but as of 2026-08-10 wuff also observes 6 Sleeper leagues
(readonly) — see the Sleeper section near the bottom of this file. When
working on keeper/draft/roster logic, check which platform the user means
before assuming Yahoo.

Before doing any keeper, draft-ranking, or roster-value analysis, read
`data/config/league_rules.json` first. It has the league's keeper rules
(no keeper cost, round 1/2 ineligible, 2-consecutive-season cap), a map of
which data file holds what, and a map of which app module owns
which piece of logic. Reading it avoids re-deriving rules by grepping
through `app/strategy.py` and `app/draft_history.py` from
scratch each session.

Key files:
- `data/config/league_rules.json` — rules + file/code map (read this first)
- `data/config/leagues.json` — cross-platform league registry (Phase 0 of docs/roadmap.md): all 7 leagues (1 Yahoo `frank-gore` + 6 Sleeper), each with platform ids + format/rules; `python3 -m app leagues` lists, `leagues-init --force` regenerates
- `app/league_registry.py` — League dataclass + registry loaders (`get_league`, `load_leagues`); keeper round rules now live on `LeagueFormat` (`keeper_ineligible_rounds`, `keeper_slot_rounds`, `keeper_slots`), not hardcoded in strategy.py
- `app/repository.py` — league-scoped data access seam: `get_repository(league_id)` serves rosters/draft history/standings/rankings for any registered league (Yahoo files or Sleeper snapshots behind one interface); web.py reads go through it, never direct JSON paths
- `app/db.py` + `app/models.py` + `app/auth.py` — multi-user state (Phase 1): SQLite via SQLAlchemy (`data/wuff.db`, gitignored; `DATABASE_URL` overrides), tables users/leagues/user_leagues/sync_runs/keeper_marks, Flask-Login with a dev email-only login (no verification — must be replaced before public deploy). Web: `/login`, `/my/leagues`, `/my/onboard` (Sleeper username → discover → import + sync)
- Keeper marking (2026-08-10): logged-in users click ☆/★ on `/keepers-board` forecast cards to mark a team's keeper (`keeper_marks` table); marks override computed picks via `league_keeper_board(keeper_prefs_override=...)` and marked players drop off the draft board. Site chrome is branded "wuff" (league name lives in the league subnav, not the header)
- ESPN import, beta (2026-08-10): `app/espn_client.py` + `app/espn_manager.py` sync ESPN leagues into the same snapshot shapes as Sleeper (`data/raw/espn/{id}/`); onboarding at `/my/onboard` (league ID; private leagues paste espn_s2/SWID, encrypted via `app/crypto.py` — set `WUFF_ENCRYPTION_KEY` in prod); views at `/espn/<id>` via the shared `league_snapshot.html`; background sweep re-syncs with stored credentials. Unofficial API — mock-validated only until a real ESPN league is imported
- Per-league keeper engine (Phase 3, 2026-08-10): `/league/<slug>/keepers` + `/league/<slug>/settings` work for any league — rules stored in `DbLeague.rules_json`, resolved by `app/league_service.resolve_league()` (DB rules merged over registry format); `league_keeper_board()` takes `draft_years`/`include_file_prefs`/`keeper_prefs_override` so nothing reads frank-gore globals; keeper cap 0 = no cap (dynasty). Keeper marks are per-league (platform + platform_league_id)
- `app/sync_scheduler.py` + `app/rate_limit.py` — background Sleeper sync (APScheduler in-process, lazy-started on first web request, `WUFF_DISABLE_SCHEDULER=1` to turn off) + global API rate budget enforced in `sleeper_client._get` (`SLEEPER_MAX_CALLS_PER_MIN`, default 600). Sync attempts audit to `sync_runs`
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

Combines multiple ranking sources in any format (JSON/CSV/PDF) into a single normalized file. **Standard source as of 2026-08-10 is `python3 -m app refresh-free-rankings`** (`app/free_rankings.py`): free FFC ADP API + Sleeper search-rank tail, QB historical adjustment auto-applied, refreshed daily by the web app's background scheduler. FantasyPros data removed 2026-08-10 (licensing — can't redistribute on a public site).

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
python3 -m app import-adp path/to/your_adp.csv
```

Saves normalized ADP to `data/raw/adp/adp_combined.json`.

## Outcome log (agent Learn pillar)

Forecast-vs-actual tracking, so scoring-method accuracy can be measured over
time instead of trusted on faith. Module: `app/outcome_log.py`.

**Write side (automatic):** `apply-qb-adjustment` and `keepers-board-export`
log every forecast they produce to `data/processed/outcome_log.json` as a
side effect — no separate step to remember. Each entry is tagged with a
`forecast_method_version` string, so accuracy can later be compared across
scoring-method changes (e.g. the QB historical adjustment standard that
replaced the old superflex+hand-tuned approach).

**Read/resolve side:** `python3 -m app resolve-outcomes` scans `pending`
entries and matches them against `data/raw/draft_history/{season}.json` —
keeper forecasts resolve against that season's keeper-slot picks (R14-15);
QB-adjustment forecasts resolve against the live-draft pick number. Entries
whose season hasn't drafted yet stay `pending`, not errored — safe to run
anytime (e.g. right after a new `draft_history/{year}.json` file is added).

**Schema per entry:** `decision_id`, `decision_type` (`keeper_forecast` /
`qb_adjustment`), `season`, `entity`, `team`, `forecast`,
`forecast_method_version`, `forecasted_at`, `actual`, `resolved_at`, `delta`,
`status` (`pending`/`resolved`). Re-forecasting the same
(decision_type, season, entity) while still `pending` overwrites in place
rather than piling up duplicates; `resolved` entries are left alone as
historical record.

**Not yet covered:** mock-draft-pick and draft-rank-vs-season-points
resolution (needs nflverse season stats matching) — logging/resolution for
those decision types is a follow-up, not yet wired in.

## Sleeper integration (2026, readonly, separate from the Yahoo league above)

Tracks 6 other leagues the user is in on Sleeper (username `wufu`), separate
from the main Yahoo league this file otherwise describes. Sleeper's public
API needs no auth (no OAuth, no approval wait) — sync just overwrites local
JSON snapshots from the live API.

- `data/config/sleeper_leagues.json` — per-league config (id, name, format:
  redraft/dynasty, season). Regenerated by `sleeper-discover` but hand-edits
  to display name/format are preserved on re-run.
- `data/raw/sleeper/{league_id}/league.json,rosters.json,draft_{id}.json` —
  synced snapshots, one dir per league.
- `data/raw/sleeper/players_cache.json` — shared Sleeper player_id → name
  lookup (~12k players); refresh via `sleeper-refresh-players` occasionally,
  not on every sync (it's a ~5MB fetch).
- `app/sleeper_client.py` — thin unauthenticated REST wrapper.
- `app/sleeper_manager.py` — sync orchestration + player_id resolution.
- CLI: `sleeper-discover <username>`, `sleeper-sync [--league-id ID]`,
  `sleeper-refresh-players`.
- Web: `/sleeper` and `/sleeper/<league_id>` routes.

**Scope is intentionally v1/visibility-only** — rosters, standings, and
draft results, no keeper-eligibility or draft-strategy logic. `strategy.py`
and `league_context.py` still assume the single Yahoo league's rules;
generalizing them for Sleeper (especially the dynasty league, which has no
round-based keeper cap at all) is unstarted future work.
