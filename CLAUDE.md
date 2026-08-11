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
- `app/roster_player.py` (2026-08-11 refactor) — `RosterPlayer` dataclass, the platform-neutral roster-player shape `strategy.py`'s keeper engine builds from any platform's repository dict. Moved out of `app/yahoo_client.py` (was `YahooRosterPlayer`, misleadingly Yahoo-branded even though `league_keeper_board()` uses it for Sleeper/ESPN too); `yahoo_client.YahooRosterPlayer` is kept as an alias for the genuinely-Yahoo-only call sites (`roster_store.py`, `mcp_client.py`, `cli.py`)
- `app/keeper_service.py` (2026-08-11 refactor) — keeper-board business logic pulled out of `web.py`: `keeper_board_state()` (single source of truth both the full-page render and the AJAX mark endpoint call), `forecast_keeper_decisions()`, `calculate_keeper_impact()`, `load_keeper_marks()`, ADP enrichment. `web.py` keeps route handlers only. Do the same pull-out-of-web.py check before porting mock draft per-league — same coupling pattern is likely still there
- `app/mock_draft.py` — per-league since 2026-08-11: `run_mock_draft(current_teams, repo=, league_format=)`. Rankings come from `repo.rankings()` via `rankings_for(repo)` — **not** the leftover `data/processed/rankings_adjusted.json`, a dead artifact of the QB-knockback method deleted 2026-08-11; don't wire it back in. Team defenses are normalized `DEF`→`DST` on the way in (`_DEF_ALIASES`); this module keys its limits on `DST` while ranking sources say `DEF`, and an unrecognized position silently gets no limit. DST/K have an earliest-draftable round (`earliest_rounds_for()`, 60%/80% into the draft) — consensus boards rank defenses around round 8 by raw value but real managers take them much later, so rank-driven BPA needs that floor or defenses cascade in the mid rounds. Team/round counts, keeper-slot rounds, starter slots and position limits all come from `LeagueFormat` (`total_draft_rounds` infers from `keeper_slot_rounds` when `draft_rounds` is unset). Position limits are derived from the league's starters (`position_limits_for()`), not a fixed table. Draft order: `build_draft_order(repo, league_format)` — the old `get_draft_order_2026*()` names are gone. Web: `/mock-draft` + `/league/<slug>/mock-draft`. **When changing the simulator, diff full output against the previous version** (`git stash` → run → compare) — the per-league port surfaced two silent-wrong-output bugs (ignored traded picks; a `best_score` floor that dropped picks) that raised no error
- **Resolving a position for a draft pick:** draft-history picks carry no position — use `nfl_stats.fantasy_position_map(season)`, **never** a plain dict comprehension over `load_rosters()`. Josh Allen (BUF QB / JAX LB) and Lamar Jackson (BAL QB / CAR-ATL DB) share names with defenders, and a naive map keeps the last row, which silently dropped this league's round-1 rushing QBs from the QB draft-slot targets and put phantom DB/LB rows in the round-1 analysis (fixed 2026-08-11). Two data limits: team defenses aren't in nflverse rosters at all, so **DST never resolves** and league history can't say when defenses go; and position resolution needs a roster snapshot, so usable seasons are 2022+ (~563 resolved picks / 4 seasons — enough for per-round aggregates, not per-pick)
- `app/draft_patterns.py` (2026-08-11) — what this league drafts and when, from its own history: `position_mix_by_round()`, `position_timing()`, `position_rank_pick_targets(position, top_n)` (generalizes the QB-only logic in `qb_historical_adjustment.py` to any position). Per-league via a repository. **Analysis only — nothing is wired to it yet**
- `app/draft_analysis.py` — per-league since 2026-08-11: both entry points take an optional `repo` (`app/repository.py`) and read that league's own draft history + standings; omit it for the default league. CLI `draft-slot-outcomes` / `position-round-outcomes` take `--league <id>`; web page at `/league/<slug>/draft-analysis`. **This is the `--league` pattern to copy** for the remaining CLI analysis commands (Phase 0 leftover in docs/roadmap.md). Both analyses correlate against final standings, so a league shows nothing until it has a season with BOTH draft results and saved standings — empty state, not an error
- `app/db.py` + `app/models.py` + `app/auth.py` — multi-user state (Phase 1): SQLite via SQLAlchemy (`data/wuff.db`, gitignored; `DATABASE_URL` overrides), tables users/leagues/user_leagues/sync_runs/keeper_marks, Flask-Login with a dev email-only login (no verification — must be replaced before public deploy). Web: `/login`, `/my/leagues`, `/my/onboard` (Sleeper username → discover → import + sync)
- Interactive keeper selection (2026-08-10, reworked 2026-08-11): `/keepers-board` (Yahoo) and `/league/<slug>/keepers` (any league) show every keeper-eligible player per team as a clickable card — click toggles kept/not-kept (thick border = kept), no login required, updates live via AJAX (no page reload). `keeper_marks` table stores per-league include/exclude overrides; `select_best_keepers()`'s `stop_auto_fill` flag stops auto-picking once a team has any live override, so 0..keeper_count kept per team are all valid end states, not just "always exactly keeper_count." See `WS-3-keeper/Keeper_Card_Interaction_Pattern.md` in the Obsidian vault for the full interaction rules before changing this UI. Site chrome is branded "wuff" (league name lives in the league subnav, not the header)
- ESPN import, beta (2026-08-10): `app/espn_client.py` + `app/espn_manager.py` sync ESPN leagues into the same snapshot shapes as Sleeper (`data/raw/espn/{id}/`); onboarding at `/my/onboard` (league ID; private leagues paste espn_s2/SWID, encrypted via `app/crypto.py` — set `WUFF_ENCRYPTION_KEY` in prod); views at `/espn/<id>` via the shared `league_snapshot.html`; background sweep re-syncs with stored credentials. Unofficial API — mock-validated only until a real ESPN league is imported
- Per-league keeper engine (Phase 3, 2026-08-10): `/league/<slug>/keepers` + `/league/<slug>/settings` work for any league — rules stored in `DbLeague.rules_json`, resolved by `app/league_service.resolve_league()` (DB rules merged over registry format); `league_keeper_board()` takes `draft_years`/`include_file_prefs`/`keeper_prefs_override` so nothing reads frank-gore globals; keeper cap 0 = no cap (dynasty). Keeper marks are per-league (platform + platform_league_id)
- `app/sync_scheduler.py` + `app/rate_limit.py` — background Sleeper sync (APScheduler in-process, lazy-started on first web request, `WUFF_DISABLE_SCHEDULER=1` to turn off) + global API rate budget enforced in `sleeper_client._get` (`SLEEPER_MAX_CALLS_PER_MIN`, default 600). Sync attempts audit to `sync_runs`
- `data/raw/rosters/yahoo_league_rosters.json` — current league rosters (updated via parse-rosters)
- `data/raw/rankings/rankings_combined.json` — combined multi-source rankings (created via combine-rankings)
- `data/raw/draft_history/{year}.json` — past draft results, one file per season
- `data/raw/draft_picks/{year}.json` — pick ownership by round for a draft year
- `app/strategy.py` — keeper eligibility/selection logic

## Keeper-picking agent (autonomous recommendations, CLI-only now)

Autonomous keeper agent recommends best 2 keepers for each team, ranks remaining draft board. As of 2026-08-11 this is a **CLI-only, not-yet-public** workflow — the web app's keeper selection lives entirely on `/keepers-board` / `/league/<slug>/keepers` (interactive cards, see above), which supersedes explaining picks to this CSV export flow for day-to-day use. This CLI path is being kept around to revisit/polish later, not deleted, but has no web page consuming its output right now (the `/keepers-board` CSV-version dropdown + `keeper_exports/` directory + `scripts/sync_keeper_board.js` were all removed 2026-08-11 as unused).

**Workflow:**
1. Update rosters: `python -m app parse-rosters`
   - Paste raw Yahoo Fantasy text (copy-pasted from browser)
   - Parser normalizes names (strips Yahoo's "player Notes" link-label artifact and any injury-status letter before it), looks up NFL teams from rankings
   - Shows preview, asks to confirm save to `yahoo_league_rosters.json`
2. Export keeper recommendations: `python -m app keepers-board-export`
   - Reads current rosters + combined rankings
   - Applies league rules (round 1/2 ineligible, 2-consecutive-season cap)
   - Scores eligible players: rank-first, VOR/keeper-years-remaining as tiebreaks
   - Outputs two CSVs (timestamped for snapshots) to `data/processed/keeper_exports/`:
     - `keepers_YYYYMMDD_HHMM.csv` — per-team picks + alternates
     - `draft_board_YYYYMMDD_HHMM.csv` — remaining board, ranked for draft prep
3. Compare snapshots: review timestamped CSVs directly (no web viewer currently) to see how recommendations shifted as rosters changed

**Keeper scoring logic:**
- Primary: overall ranking (market consensus)
- Tiebreak 1: value over replacement rounds (positional scarcity for this league's roster shape)
- Tiebreak 2: keeper years remaining (players with multi-year runway preferred)
- Never: rank-based QB bypass (non-rushing QBs stay lower than WR2/WR3 tier even if ranked higher)

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

### Removed: `adjust-rankings` / `ranking_adjustments.py` (2026-08-11)

The old hand-tuned QB-knockback path is **deleted**: the `adjust-rankings` CLI
command, `app/ranking_adjustments.py`, and `data/config/board_adjustments.json`
are gone. It had been superseded by `refresh-free-rankings` +
`qb_historical_adjustment.py`, and its last consumer (the mock draft) moved to
`repo.rankings()`. Leftover `data/processed/rankings_adjusted*.{json,csv}`
files are gitignored and were left on disk — they are dead artifacts, don't
wire them back into any board.

One piece was kept: `load_qb_rushing_yards()` moved to `app/nfl_stats.py`. It's
the only rushing-production lookup in the codebase, and
`keeperRules.behavioralNotes` in `league_rules.json` calls this league's
rushing-QB round-1 premium a manual judgment override *for want of exactly this
data* — so it's the starting point if that ever gets automated.

### ADP import (`app/adp_manager.py`)

Imports Average Draft Position (market consensus) from a CSV, used to enrich
keeper forecasts and mock draft picks with an ADP field.

```bash
python3 -m app import-adp path/to/your_adp.csv
```

Saves normalized ADP to `data/raw/adp/adp_combined.json`.

## Outcome log (agent Learn pillar)

Forecast-vs-actual tracking, so scoring-method accuracy can be measured over
time instead of trusted on faith. Module: `app/outcome_log.py`. Per-league
since 2026-08-11 (was Yahoo/global-only) — see below.

**Write side (automatic):** `apply-qb-adjustment` and `keepers-board-export`
(CLI, Yahoo-only) log every forecast they produce to
`data/processed/outcome_log.json` as a side effect — no separate step to
remember. `app/keeper_service.py`'s `log_team_keeper_forecast()` does the
same for the web keeper board (`/keepers-board/mark`, any league) after
every click — this is the path that actually covers non-Yahoo leagues. Each
entry is tagged with a `forecast_method_version` string, so accuracy can
later be compared across scoring-method changes (e.g. the QB historical
adjustment standard that replaced the old superflex+hand-tuned approach, or
`web_keeper_board_v1` vs the CLI's `rank_first_vor_years_remaining_v1`).

**Per-league scoping (2026-08-11):** every entry carries `platform` +
`platform_league_id` (same convention as the `KeeperMark` table). The
default Yahoo league (`platform='yahoo'`, id `'9410'`) keeps unscoped
`decision_id`s for backward compatibility with pre-2026-08-11 entries; any
other league gets a `{platform}-{platform_league_id}_` prefix so leagues
never collide in the one shared log file.

**Read/resolve side:** `python3 -m app resolve-outcomes` scans `pending`
entries, groups them by league, and resolves each group against that
league's own `draft_years` via `app/repository.py` (a Sleeper/ESPN league
resolves against its own synced draft, not Yahoo's `draft_history/` files;
legacy/default-league entries still use the direct loader) — keeper
forecasts resolve against that season's keeper-slot picks; QB-adjustment
forecasts resolve against the live-draft pick number. Entries whose season
hasn't drafted yet stay `pending`, not errored — safe to run anytime (e.g.
right after a new `draft_history/{year}.json` file is added, or a league's
draft gets synced).

**Schema per entry:** `decision_id`, `decision_type` (`keeper_forecast` /
`qb_adjustment`), `platform`, `platform_league_id`, `season`, `entity`,
`team`, `forecast`, `forecast_method_version`, `forecasted_at`, `actual`,
`resolved_at`, `delta`, `status` (`pending`/`resolved`). Re-forecasting the
same (platform, platform_league_id, decision_type, season, entity) while
still `pending` overwrites in place rather than piling up duplicates;
`resolved` entries are left alone as historical record.

**Not yet covered:** nothing reads the log back to actually adjust scoring
yet (see README's "What's next" — that's the real Learn-pillar step this
slice sets up but doesn't do). Also still open: mock-draft-pick and
draft-rank-vs-season-points resolution (needs nflverse season stats
matching) — logging/resolution for those decision types is a follow-up, not
yet wired in.

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
