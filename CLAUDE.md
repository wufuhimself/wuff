# wuff — Fantasy Football Helper (Yahoo + Sleeper)

## Git workflow

Solo repo, one dev. Commit directly to `main` for features and fixes — no
PRs, no feature branches needed as standard workflow. Branch only if user
explicitly asks for one (e.g. testing something risky, or ultrareview
wants a diff target). If already on a feature branch, ask before merging
vs continuing there.

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
- `app/yahoo_models.py` + `app/yahoo_store.py` + `app/yahoo_migrate.py` (2026-08-12) — the frank-gore league's **hand-curated data lives in the database now**, not `data/raw/`: draft history, pick ownership, standings, league rosters. `data/raw/` is gitignored and the deploy's filesystem is ephemeral, so those JSON files never reached production and every page backed by them rendered empty *silently*. `YahooDbRepository` serves them behind the unchanged `LeagueDataRepository` interface. Load/refresh with `python3 -m app migrate-yahoo-data` (reads the local JSON, writes whatever `DATABASE_URL` points at — run it from a machine that HAS the JSON; the deployed container does not). `parse-rosters` writes the DB and keeps the JSON as a local working copy. **Verify any change here with `python3 scripts/compare_yahoo_backends.py`** — it asserts the JSON and DB backends return equal output for every method and every year, which is the diff-the-full-output discipline the mock-draft port established. Deliberately still JSON: `rankings/yahoo_rankings.json` (rewritten daily by `refresh_free_rankings()`, self-heals), and `managers/` + `season_rosters/`. ⚠️ **`managers/{year}.json` is not dead data — do not delete it.** It carries one row per manager per season *with their email*, which is the only persistent owner id this league has, and `franchise-alias-template --from-managers` turns it into the committed `data/config/franchise_aliases.json` that gives Yahoo cross-season manager identity (see the franchise section below). Nothing reads it at runtime — it is a generator input, like the nflverse CSVs behind `snapshot-position-map` — so it only has to exist on a machine that regenerates that file
- `app/franchise_registry.py` + `app/franchise_store.py` + `app/franchise_models.py` (2026-08-13, Phase 5 step 2) — **franchise (team) identity**, the stable team key that replaces display-name-as-key. Sleeper/ESPN resolve from `ownerId` (fallback `rosterId`), which the snapshots always carried and the repository was dropping. Yahoo resolves from **manager emails** in `data/raw/managers/{year}.json` via `franchise-alias-template --from-managers --write`, which generates the committed `data/config/franchise_aliases.json` (team names + slugs only, never emails) — frank-gore: 48 name-lineages → 15 real managers, `manager-report` 24 rows → 14. Build/refresh with `python3 -m app build-franchises`, which also stamps `KeeperMark.franchise_id`. That column sits **alongside** `team_name`, never replacing it: NULL falls back to name matching, so old rows keep working while renames stop orphaning marks. Only *exact* name links are made automatically (identical slugs; a roster name whose tail after `" - "` matches a known team) — never similarity, which is the algorithmic-manager-identity mistake this project already made once. **Verify changes with `python3 scripts/check_franchise_identity.py`** — throwaway SQLite, real marks, renames a team on each platform's own terms and asserts the marks follow
- `app/domain.py` (2026-08-13, Phase 5 step 3) — **typed shapes the repository serves**: `RosterTeam`/`RosterEntry`/`DraftPick`/`StandingRow`/`RankingRow`, each carrying `canonical_player_id` (player registry) and `franchise_id` (franchise registry). The dict API's "normalized shape" was only ever a docstring and the backends really did disagree inside it (Yahoo rosters have `teamId`/`ownerName`, Sleeper's have `rosterId`/`ownerId`/`starters`/records; **Sleeper standings have no `rank` field at all**). Typed methods (`roster_teams()`, `drafts()`, `standing_rows()`, `ranking_rows()`) are implemented **once on the `LeagueDataRepository` base class** in terms of the dict methods, so a backend can't drift by forgetting a field. `raw` on each type is the original dict — a migration aid, delete it when nothing reads it. Consumers migrate one per commit; `draft_patterns` is the first and the pattern to copy. **Verify with `python3 scripts/check_repository_contract.py`** (14,425 assertions over every backend × league)
- `app/bye_weeks.py` (2026-08-13, Phase 5 step 5) — bye weeks are **not a field anywhere upstream**, they're derived: a team's bye is the one regular-season week nflverse's schedule has no game for. Checked against 2020-2026 before trusting it — every team has exactly one such week every season except 2022, where BUF/CIN each show two because their week-17 game was cancelled outright (Damar Hamlin), not rescheduled; only a clean single answer is trusted, so those two correctly report no bye rather than a guess. Team codes are run through `player_registry.normalize_team()` on both the write and read side — nflverse spells the Rams `LA`, the registry resolves rosters to `LAR`, and a raw key would have silently zeroed out every Rams player's bye. Committed `data/config/nfl_bye_weeks.json` is the same self-healing snapshot pattern as `nfl_position_map.json`, with a matching daily scheduler job (`nfl-schedule-daily`). CLI: `fetch-schedules`, `snapshot-byes`. `RosterEntry.status`/`.injury_status` (Phase 5 step 5, same commit) read off the **resolved player identity**, not the platform's own roster row — no backend actually populates a status field, so this was the only way it was ever going to work for anyone
- **Sleeper transactions** (2026-08-13, Phase 5 step 6) — `app/sleeper_client.get_league_transactions()` wraps `/league/{id}/transactions/{week}`; `sleeper_manager.sync_transactions()` walks weeks 0-18 every sync (Sleeper exposes no "current week" field, and an empty response is a cheap way to know "hasn't happened yet"), wired into `sync_league()` so every existing sync path picks it up automatically — no new call site to remember. **Sleeper-only**: ESPN has no transaction endpoint wrapped, Yahoo is still blocked. `app/domain.py`'s `Transaction`/`TransactionMove`/`TransactionPickMove`: a trade moving 2 players is 4 `TransactionMove`s (add+drop per side), not one row with two player lists — covers trade/waiver/free_agent with one shape. `repository.transactions()` (typed-once, like steps 3-5) resolves player moves via `by_platform_id('sleeper', ...)` (exact — the payload has Sleeper's own id) and attributes both moves and traded picks by **roster_id, not team name**, so a mid-season rename can't misattribute an older transaction to the current name. Verified against all 6 real leagues (17 transactions) including a picks-only trade and a same-franchise add+drop waiver before trusting it. **Verify with `python3 scripts/check_repository_contract.py`** (14,520 checks)
- **Sleeper matchups** (2026-08-13, Phase 5 step 7) — weekly head-to-head scores, Sleeper-only, **THIS SEASON ONLY** (Sleeper chains history across seasons via `previous_league_id` under a *different* league_id — deliberately not walked; confirmed with the user first). ⚠️ **Scope correction, decided before building**: this does NOT read `LeagueFormat.scoring` to compute points — Sleeper's real scoring settings run ~130 rule keys (IDP, special teams, per-bracket defense bonuses) against `LeagueFormat`'s 9, so recomputing would silently diverge from what the league actually sees. `MatchupSide.points` trusts Sleeper's own computed total directly; a real scoring engine is future work only if `LeagueFormat.scoring` itself needs modeling (e.g. a points *projection*, a different problem). `sync_matchups()` bounds its fetch by `settings.leg` (confirmed correct on a completed season: 17 weeks, `leg=17`, not reset) rather than a fixed range like transactions — an unplayed matchups week is NOT an empty response like transactions gets, it's real rows with every score at `0.0`. `repository.matchups()` drops any week where every side is still `0.0` (documented as a heuristic, not guaranteed — checked against a real completed 2025 season first: 17 real weeks, no phantom all-zero 18th). Franchise attribution via roster_id, same reasoning as transactions. Verified against a real completed season (synced on demand, not registered): 82 matchups/17 weeks, 100% franchise resolution, 1639/1640 starters resolved (the one gap is Sleeper's own `'0'` empty-slot placeholder, already filtered elsewhere). **Verify with `python3 scripts/check_repository_contract.py`** (14,528 checks, includes a dedicated completed-season check since every registered league's own assertions pass vacuously on zero rows pre-kickoff)
- **Sleeper playoffs** (2026-08-13, Phase 5 step 9, closes the phase) — bracket structure only, Sleeper-only. `sleeper_manager.sync_playoffs()` wraps `get_league_winners_bracket()`/`get_league_losers_bracket()` (already existed, unused), wired into `sync_league()`. Bracket **structure** exists from the moment `playoff_teams` is configured — well before `playoff_week_start`, `w`/`l`/`t1`/`t2` null until played — so a not-yet-decided bracket is a real state, not an error. `app/domain.py`'s `PlayoffMatch`/`BracketSource` deliberately carry only `franchise_id`, never points or team names — a playoff week's games are also ordinary `Matchup`s from step 7; join on `(season, week, franchise_id)` for those. No week number either — Sleeper's payload has none. ⚠️ **Real bug caught by testing against actual data, not by re-reading the code**: Sleeper's `t1_from`/`t2_from` resolve INDEPENDENTLY per side — a real championship match here has home from "winner of match 3" *and* away from "winner of match 4" simultaneously. First cut used one match-level field with an `or` chain, which silently dropped whichever reference came second. Fixed with per-side `home_from`/`away_from`, each its own `BracketSource`. Verified against an in-progress bracket (round 1 seeded, later rounds correctly `None`) and a real completed season (11/11 decided, championship winner cross-checked by hand against raw owner ids). **This closes every Phase 5 step** — nothing outside `repository.py` reads `Matchup`/`PlayoffMatch`/`Transaction` yet; surfacing them in web pages/CLI/outcome-log is deliberately the next phase, not left undone here. **Verify with `python3 scripts/check_repository_contract.py`** (14,572 checks)
- `/league/<slug>/matchups` (2026-08-13) — first page to read `Matchup`/`PlayoffMatch`: weekly scores + winners/losers bracket, read-only. Follows the standard per-league route pattern (`_member_league`, `_league_page_ctx`, `repository_for(league)`) — nothing new invented. `PlayoffMatch` carries only `franchise_id`, so the view resolves team names once against `FranchiseRegistry` rather than teaching the template a lookup. Sleeper-only in practice; Yahoo/ESPN show the existing empty state. Smoke-tested through the real Flask app + real synced data before shipping — caught two bugs in the smoke test itself along the way (checking for the literal string "TBD" hit the explanatory caption, not real bracket cells; searching for "Winners bracket" found nothing because the heading is CSS-capitalized, not literal capitalized text in the HTML)
- `app/outcome_models.py` + `app/outcome_store.py` (2026-08-20) — **the outcome log lives in the database now**, not `data/processed/outcome_log{,_history}.json`. Same landmine as the Sleeper/ESPN snapshots and the Scouting checkpoints: `data/processed/` is gitignored and Railway's disk is ephemeral, so every forecast the deployed app logged was wiped by the next redeploy — silently, and worse than the snapshot case because a snapshot re-syncs from the platform API while a forecast history cannot be re-derived from anything. `outcome_log.py`'s four storage functions swapped bodies; every caller (`keeper_service`, `cli`, `agent_reasoning`, `scripts/langgraph_spike`) is unchanged. **Not** a DB-in-prod/files-locally split — one backend everywhere, since every other piece of user state already lives in the local SQLite DB; the JSON files are migration input only (`python3 -m app migrate-outcome-log`, run once per database from a machine that still HAS the files). Two tables, not one with a flag: an outcome row is mutable (upserted while pending, resolved in place), a history row is append-only with many rows per `decision_id`. `OutcomeEntry.seq` exists because insertion order **cannot** be derived from `forecasted_at` — an upsert rewrites that field, which would reshuffle the list every time a pending forecast changed. Pre-2026-08-11 entries carry no `platform`/`platform_league_id`; `outcome_store.normalize_entry()` fills in the documented Yahoo default on the way in (verified: all 4 keep their exact `decision_id`). ⚠️ **Real pre-existing bug this surfaced**: `log_outcome()` on the upsert path returned early without ever calling `save_outcomes()`, so an owns-the-list call that *changed* a forecast wrote its history row and then dropped the change. Unreachable in production (every caller batches via `outcomes=`), fixed anyway. **Verify with `python3 scripts/compare_outcome_backends.py`** — throwaway SQLite, the real local log as reference, and unlike the Yahoo gate it exercises the **write** paths (upsert, identical re-log, new entry, `resolve_outcomes`), which is where the two backends can actually diverge
- `app/repository.py` — league-scoped data access seam: `get_repository(league_id)` serves rosters/draft history/standings/rankings for any registered league (Yahoo files or Sleeper snapshots behind one interface); web.py reads go through it, never direct JSON paths
- `app/roster_player.py` (2026-08-11 refactor) — `RosterPlayer` dataclass, the platform-neutral roster-player shape `strategy.py`'s keeper engine builds from any platform's repository dict. Moved out of `app/yahoo_client.py` (was `YahooRosterPlayer`, misleadingly Yahoo-branded even though `league_keeper_board()` uses it for Sleeper/ESPN too); `yahoo_client.YahooRosterPlayer` is kept as an alias for the genuinely-Yahoo-only call sites (`roster_store.py`, `mcp_client.py`, `cli.py`)
- `app/keeper_service.py` (2026-08-11 refactor) — keeper-board business logic pulled out of `web.py`: `keeper_board_state()` (single source of truth both the full-page render and the AJAX mark endpoint call), `forecast_keeper_decisions()`, `calculate_keeper_impact()`, `load_keeper_marks()`, ADP enrichment. `web.py` keeps route handlers only. Do the same pull-out-of-web.py check before porting mock draft per-league — same coupling pattern is likely still there
- `app/mock_draft.py` — per-league since 2026-08-11: `run_mock_draft(current_teams, repo=, league_format=)`. Rankings come from `repo.rankings()` via `rankings_for(repo)` — **not** the leftover `data/processed/rankings_adjusted.json`, a dead artifact of the QB-knockback method deleted 2026-08-11; don't wire it back in. Team defenses are normalized `DEF`→`DST` on the way in (`_DEF_ALIASES`); this module keys its limits on `DST` while ranking sources say `DEF`, and an unrecognized position silently gets no limit. DST/K have an earliest-draftable round (`earliest_rounds_for()`, 60%/80% into the draft) — consensus boards rank defenses around round 8 by raw value but real managers take them much later, so rank-driven BPA needs that floor or defenses cascade in the mid rounds. Team/round counts, keeper-slot rounds, starter slots and position limits all come from `LeagueFormat` (`total_draft_rounds` infers from `keeper_slot_rounds` when `draft_rounds` is unset). Position limits are derived from the league's starters (`position_limits_for()`), not a fixed table. Draft order: `build_draft_order(repo, league_format)` — the old `get_draft_order_2026*()` names are gone. Web: `/mock-draft` + `/league/<slug>/mock-draft`. **When changing the simulator, diff full output against the previous version** (`git stash` → run → compare) — the per-league port surfaced two silent-wrong-output bugs (ignored traded picks; a `best_score` floor that dropped picks) that raised no error
- **Resolving a position for a draft pick:** draft-history picks carry no position — use `nfl_stats.fantasy_position_map(season)`, **never** a plain dict comprehension over `load_rosters()`. Josh Allen (BUF QB / JAX LB) and Lamar Jackson (BAL QB / CAR-ATL DB) share names with defenders, and a naive map keeps the last row, which silently dropped this league's round-1 rushing QBs from the QB draft-slot targets and put phantom DB/LB rows in the round-1 analysis (fixed 2026-08-11). Two data limits: team defenses aren't in nflverse rosters at all, so **DST never resolves** and league history can't say when defenses go; and position resolution needs a roster snapshot, so usable seasons are 2022+ (~563 resolved picks / 4 seasons — enough for per-round aggregates, not per-pick)
- `app/ranking_history.py` (2026-08-11) — dated snapshots of the rankings board under `data/raw/rankings/history/{YYYY-MM-DD}.json`, written by every `refresh_free_rankings()`. The refresh overwrites its other outputs, so this is the **only** record that a player used to rank differently, and it **cannot be backfilled**. `annotate_with_movement()` adds `trend`/`rankingDelta`/`adpDelta` to board rows. `trend` derives from ranking not ADP (only ~256 of 556 players have ADP). ⚠️ `data/raw/` is gitignored and container filesystems are ephemeral — needs a volume or DB move before the Phase 4 deploy, and the failure mode is silent (trends just stop rendering)
- `app/board_service.py` + `BoardAdjustment` (2026-08-11) — per-**user** manual draft-board nudges (▲/▼/↺ on `/keepers-board` and `/league/<slug>/keepers`, login required). Stored as a signed **offset**, never a pinned rank: the base board regenerates daily, so an absolute position would freeze a stale opinion and stop reflecting new data. `adjusted = base_order - offset`, re-sorted, renumbered gap-free, applied **before** the top-100 truncation so a player at 150 can be pulled into view. Note this is per-user unlike `keeper_marks`, which is per-league and shared. **The sort tiebreak must consider offset direction** — moving up one spot ties the player above, and breaking that tie by base order alone made the first ▲ press appear to do nothing (fixed 2026-08-11); test exact expected positions, not just "something moved"
- `app/draft_patterns.py` (2026-08-11) — what this league drafts and when, from its own history: `position_mix_by_round()`, `position_timing()`, `position_rank_pick_targets(position, top_n)` (generalizes the QB-only logic in `qb_historical_adjustment.py` to any position). Per-league via a repository. Surfaced at `/league/<slug>/draft-patterns` — describes *behaviour* (what goes when), as opposed to `/league/<slug>/draft-analysis` which asks whether draft decisions predicted the final standings
- `app/draft_analysis.py` — per-league since 2026-08-11: both entry points take an optional `repo` (`app/repository.py`) and read that league's own draft history + standings; omit it for the default league. CLI `draft-slot-outcomes` / `position-round-outcomes` take `--league <id>`; web page at `/league/<slug>/draft-analysis`. **This is the `--league` pattern to copy** for the remaining CLI analysis commands (Phase 0 leftover in docs/roadmap.md). Both analyses correlate against final standings, so a league shows nothing until it has a season with BOTH draft results and saved standings — empty state, not an error
- `app/manager_report.py` (2026-08-12) — per-manager grading, built on `draft_analysis.py`'s slot-vs-rank baseline: `value_over_expected` = this league's own baseline avg finish for a manager's draft slots minus their actual avg finish. Web `/league/<slug>/manager-report`, CLI `manager-report --league <id>`. Deliberately does not grade individual picks against season fantasy points (available in `data/raw/nfl_stats/seasonal/{year}.csv` — tempting, don't use it here) — that's the rejected ADP-vs-outcome "gem-finding" shape. ⚠️ **Cross-season manager identity does not resolve reliably** — checked against real data before shipping: a 12-team league across 5 seasons produced 24 "manager" rows, not ~12, because Yahoo's rename note (the only identity signal available, no persistent owner id is saved anywhere) rarely fires. Shipped anyway with every row carrying `team_names` (every raw name folded in) so the limit is visible, not hidden. Don't try to re-derive identity algorithmically again — it's already been tried and the data doesn't support it; a hand-authored alias file is the actual fix, not built speculatively
- `app/db.py` + `app/models.py` + `app/auth.py` — multi-user state (Phase 1): SQLite via SQLAlchemy (`data/wuff.db`, gitignored; `DATABASE_URL` overrides), tables users/leagues/user_leagues/sync_runs/keeper_marks, Flask-Login with a dev email-only login (no verification — must be replaced before public deploy). Web: `/login`, `/my/leagues`, `/my/onboard` (Sleeper username → discover → import + sync). ⚠️ `db.py`'s `_COLUMN_BACKFILLS` is the poor-man's migration for columns added after a table shipped — it now runs on **Postgres too**, not just SQLite (prod's tables were created by an earlier `create_all`, so a later column exists in the model and not in the database, and the failure is an `UndefinedColumn` 500 on every page that queries it)
- `app/membership.py` (2026-08-12) — **which leagues a user may see, and which one is theirs by default.** Replaces `league_registry.default_league_id()` as what the un-scoped pages (`/`, `/keepers-board`, `/mock-draft`, `/standings`, `/draft-history`, `/draft-picks`, `/draft-order`) resolve to: before this, *every* logged-in user landed on frank-gore, so a second real account saw my league. Membership is a `user_leagues` row keyed on (platform, platform_league_id) — same key as `KeeperMark`/`BoardAdjustment`, never the wuff slug. `User.default_league_slug` stores the choice (a slug, not a `leagues.id` FK, because frank-gore lives in `leagues.json` and may have no DB row); NULL falls back to the user's first followed league, and a user who follows none has **no** default and gets sent to onboarding — there is deliberately no global fallback, since that's exactly the leak being closed. Web: `/my/leagues` has a "Make default" button (`/my/leagues/default`), per-league routes and `/sleeper/<id>` + `/espn/<id>` 302 to `/leagues` when the league isn't yours, and `/leagues` lists only your leagues. `/keepers-board` and `/mock-draft` take `?league=<slug>` so a user whose default is a Sleeper league can still open the Yahoo league they follow instead of ping-ponging between the two pages. **Registry leagues reach an account only via `python3 -m app grant-league --email X --league <slug> [--default]`** — nothing "imports" them, so without a grant they have no follower and are visible to nobody (deliberately CLI-only: a claim button on a public deploy means the first stranger to click it gets my league). Verify changes with `python3 scripts/check_league_scoping.py` — throwaway SQLite, real requests, asserts each user gets their own league and no one else's
- Interactive keeper selection (2026-08-10, reworked 2026-08-11): `/keepers-board` (Yahoo) and `/league/<slug>/keepers` (any league) show every keeper-eligible player per team as a clickable card — click toggles kept/not-kept (thick border = kept), updates live via AJAX (no page reload). **Login required — as of 2026-08-12 the whole app is** (`_require_login` in `web.py` gates every endpoint except `login`/`login_verify`/`logout`/`static` via an allowlist, so a new route is private by default; before that, any anonymous visitor could read the default league's rosters, standings and draft history). `keeper_marks` table stores per-league include/exclude overrides; `select_best_keepers()`'s `stop_auto_fill` flag stops auto-picking once a team has any live override, so 0..keeper_count kept per team are all valid end states, not just "always exactly keeper_count." See `WS-3-keeper/Keeper_Card_Interaction_Pattern.md` in the Obsidian vault for the full interaction rules before changing this UI. Site chrome is branded "WuFF" (league name lives in the league subnav, not the header). A wizard/"Gridiron Sage" persona theme was added and reverted the same day (2026-08-11) — user copy stays plain and factual, no personas or mascot voice
- ESPN import, beta (2026-08-10): `app/espn_client.py` + `app/espn_manager.py` sync ESPN leagues into the same snapshot shapes as Sleeper (`data/raw/espn/{id}/`); onboarding at `/my/onboard` (league ID; private leagues paste espn_s2/SWID, encrypted via `app/crypto.py` — set `WUFF_ENCRYPTION_KEY` in prod); views at `/espn/<id>` via the shared `league_snapshot.html`; background sweep re-syncs with stored credentials. Unofficial API — mock-validated only until a real ESPN league is imported
- Per-league keeper engine (Phase 3, 2026-08-10): `/league/<slug>/keepers` + `/league/<slug>/settings` work for any league — rules stored in `DbLeague.rules_json`, resolved by `app/league_service.resolve_league()` (DB rules merged over registry format); `league_keeper_board()` takes `draft_years`/`include_file_prefs`/`keeper_prefs_override` so nothing reads frank-gore globals; keeper cap 0 = no cap (dynasty). Keeper marks are per-league (platform + platform_league_id)
- `app/sync_scheduler.py` + `app/rate_limit.py` — background Sleeper sync (APScheduler in-process, lazy-started on first web request, `WUFF_DISABLE_SCHEDULER=1` to turn off) + global API rate budget enforced in `sleeper_client._get` (`SLEEPER_MAX_CALLS_PER_MIN`, default 600). Sync attempts audit to `sync_runs`
- `data/raw/rosters/yahoo_league_rosters.json` — current league rosters (updated via parse-rosters)
- `data/raw/rankings/rankings_combined.json` — combined multi-source rankings (created via combine-rankings)
- `data/raw/draft_history/{year}.json` — past draft results, one file per season
- `data/raw/draft_picks/{year}.json` — pick ownership by round for a draft year
- `app/strategy.py` — keeper eligibility/selection logic
- `app/templates/base.html` (2026-08-14) — all page CSS lives inline here, no separate stylesheet/framework; every template extends this one, so styling changes here cascade app-wide. `.button` upgraded from flat fill to gradient + shadow + hover lift; two opt-in variants added (`.button-secondary` outlined, `.button-ghost` text-only). No CSS framework adopted — Flask doesn't require one, but the app wasn't asking for a framework swap, just nicer buttons, so this stayed a targeted CSS edit. ⚠️ **`.button-ghost`/`.button-secondary` are modifiers, not standalone classes** — they assume the base `.button` class already applied (padding/radius/sizing) and only override background/color/shadow. Used alone (`class="button-ghost"`) a button renders as a bare bordered box; always pair as `class="button button-ghost"` (bug hit and fixed same day, in the leagues-page merge below)
- **Leagues/My leagues nav merge** (2026-08-14) — `/leagues` (grouped by platform) and `/my/leagues` (flat, with sync/default-league actions) had become the same "your leagues" list shown two ways once `app/membership.py` scoped both to the caller — merged into one page at `/leagues` (grouped by platform + the sync/default actions). `/my/leagues` and its POST actions now redirect to `/leagues`; the Flask endpoint name `my_leagues` was kept (only the route body changed) so `url_for('my_leagues')` call sites didn't need touching. Header nav: the two "Leagues"/"My leagues" text links are gone, replaced by an email profile menu (`<details>/<summary>`, no JS) listing followed leagues + "All leagues" + Log out, plus a standing "Import league" button. `nav_leagues` context var feeds the dropdown app-wide via the existing `_inject_league_context` processor
- **Per-league nav unified across platforms** (2026-08-14) — Yahoo's own dashboard (`/`, `/keepers-board`, `/mock-draft`, `/draft-history`, `/standings`) had a hardcoded 5-item nav; every `/league/<slug>/...` page (Sleeper, ESPN, and Yahoo reached that way) already had 8 (+ Matchups/Draft patterns/Draft analysis/Manager report/Settings) — confirmed live those 5 pages already worked for Yahoo's `frank-gore`, just weren't linked. Both nav branches in `base.html` now show all 8 tools; `/draft-history`/`/standings` only appear on the generic branch when the league being viewed **is** the caller's default (those two routes always resolve via `_default_repo()` to the default league regardless of which league page you're on — showing them unconditionally would silently jump to a different league's data). New `default_league_id` context var lets Yahoo's nav build `/league/frank-gore/...` links without hardcoding the slug
- **Sleeper draft dates + draft-state nav ordering** (2026-08-20) — Sleeper returns 16 fields per draft including `start_time`; the snapshot kept 5 and dropped it, and `sync_league()` skipped writing a snapshot at all unless status was `complete`/`in_progress`, so a *scheduled but undrafted* league had no draft row — the one state where keeper decisions still matter. Now every draft is snapshotted (only the pick fetch is conditional) with `startTime`. ⚠️ `start_time` is epoch **milliseconds**; read as seconds it lands in 1970, renders happily, and is wrong — the contract asserts the parsed year is 2000-2100, not merely that a date exists. `domain.DraftSchedule` + `repository.draft_schedules()`/`next_draft_schedule()`/`has_drafted()`, typed once on the base class. **Kept out of `draft_years()` on purpose** (and asserted both ways): that dict means "seasons this league actually drafted", and an empty season in it moves `keeper_service._next_draft_season()`'s `max(keys)+1` forward a year and feeds a phantom season to `strategy.py`'s keeper-eligibility walk. Nav order is now conditional on `has_drafted()` — pre-draft leads with Keepers/Draft, post-draft with Matchups/Transactions, History always last, nothing hidden either way. ⚠️ **`has_drafted()` must stay cheap** — it runs in a context processor on every render; it uses `draft_schedules()`/`draft_years()` and never the typed `drafts()`, which costs ~255ms for frank-gore because it resolves every pick against the player registry. `web._league_has_drafted()` caches per request in Flask's `g`. Its `season` falls back to `max(drafted)+1` when the league has none (frank-gore's is None) — "has this league ever drafted" would call it drafted off 2020-2025 history while its 2026 draft is still ahead
- **Overview pages merged into one template** (2026-08-14) — `dashboard.html` (Yahoo, `/`) and `league_snapshot.html` (Sleeper/ESPN, `/sleeper/<id>`, `/espn/<id>`) had drifted into showing different info for no platform reason (only Yahoo showed next-season draft order, only Sleeper/ESPN showed completed draft picks inline) and each built its own ad-hoc standings/roster dict by hand with different field names for the same data. Replaced both with `league_overview.html` + `_league_overview_ctx()` in `web.py`, built on the **typed** repository methods (`roster_teams()`, `standing_rows()`, `drafts()`, Phase 5) instead — standings join to rosters via `franchise_id` now, not the old Yahoo-only `' - '.rsplit()` team-name-lineage hack. Roster cards split starters/bench only when the platform actually reports a lineup (Sleeper/ESPN); Yahoo's parsed rosters never do (confirmed live — `roster_parser.py` never sets `selectedPosition`), so they render as one flat position-sorted list, same as before. Lineup slot labels (FLEX/SUPER_FLEX, not just the starter's own position) come from the platform's own `rosterPositions` snapshot list, passed through explicitly rather than dropped. Both draft sections (next-season order, completed picks) now show on every platform, gated on having the data. Caught by testing against real data, not by re-reading the code: Yahoo's completed-draft picks carry no position (documented limit, see `league_rules.json`) — the Pos column now hides per-draft when nothing in it has one, instead of rendering empty; Sleeper's `pointsAgainst` is `null` (not `0`) pre-season, which rendered as the literal string "None" in the standings table — pre-existing bug in both old templates, now shows "—". Deleted `dashboard.html`, `league_snapshot.html`, and the two per-platform roster-shaping helpers they were the only callers of

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

**Read side (2026-08-11):** `accuracy_report()` groups resolved entries by
(platform, platform_league_id, decision_type, forecast_method_version) and
reports `hit_rate` for `keeper_forecast` (delta is 0/1) or `mean_delta` +
`mean_abs_delta` for `qb_adjustment` (delta is a signed pick-count miss).
CLI: `python3 -m app outcome-accuracy [--league <id>]` (omit `--league` for
every league in the shared log — cross-league method comparison, not a
per-league default, since that's the whole point of one shared file). As of
2026-08-11 every entry in the log is still `pending` — no season has
drafted yet this cycle — so this reports "no resolved forecasts yet" for
every group; that's expected, not a bug, run `resolve-outcomes` again after
a draft happens.

**Still not covered:** nothing yet takes `accuracy_report()`'s numbers and
feeds them back into a scoring weight — that's the actual next Learn-pillar
step, and it stays undone on purpose until there's real resolved volume to
tune against (tuning scoring on 0 resolved entries, or even a handful, would
just be hand-tuning on noise wearing a data costume). Also still open:
mock-draft-pick and draft-rank-vs-season-points resolution (needs nflverse
season stats matching) — logging/resolution for those decision types is a
follow-up, not yet wired in.

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
