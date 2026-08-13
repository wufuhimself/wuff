# wuff Roadmap — Multi-Tenant Product

**Direction set 2026-08-10:** turn wuff from a single-user local tool into a
web product anyone can use to import their Yahoo, Sleeper, or ESPN leagues
and run the keeper/draft/roster analysis on them.

**Decisions made:**
- **Goal:** a real, demoable product (interview-quality); organic adoption is
  a bonus, not the bar.
- **Stack:** keep Flask + server-rendered templates. No React rewrite unless
  users demand it.
- **Yahoo: deprioritized (2026-08-10).** Yahoo is dragging its feet even on
  read-only API access (approval pending since ~2026-07-17). Not worth
  planning around — ESPN becomes the second import platform; Yahoo lands
  whenever Yahoo cooperates.
- **MVP platform:** Sleeper first (no OAuth, no approval wait, client already
  built in `app/sleeper_client.py`).

This supersedes the Option A / Option B framing in `hosting-plan.md` — the
choice is effectively Option B (multi-user interactive), but sequenced to
dodge the OAuth wall by launching Sleeper-only.

---

## Where wuff is today

One-user tool. Flat JSON files under `data/` (see `app/paths.py`). One Yahoo
token, one Sleeper username. League rules hardcoded for the Frank Gore
Memorial League in `app/strategy.py` / `app/league_context.py`. Rosters
updated by paste (`parse-rosters`), rankings imported by hand from CSV/PDF.
Flask app is a read-only viewer with no login, run locally.

Four structural walls between here and a public product:

1. **Multi-tenancy** — accounts, per-user data isolation, database instead of
   JSON files.
2. **Platform abstraction** — one normalized League/Roster/Draft/Player
   model with three importers behind it.
3. **Rules engine** — keeper logic driven by per-league config, not
   hardcoded Frank Gore rules.
4. **Rankings licensing** — FantasyPros data cannot be redistributed to
   strangers. Legal wall, not a code wall.

---

## Phase 0 — Untangle (no new features)

Make the codebase multi-league-shaped before adding users.

- ✅ **League as a first-class object** (2026-08-10): `app/league_registry.py`
  + `data/config/leagues.json` register all 7 leagues (1 Yahoo + 6 Sleeper)
  with wuff-internal ids. CLI: `leagues`, `leagues-init`.
- ✅ **Rules schema** (2026-08-10): keeper round rules moved from
  `strategy.py` hardcoded constants to per-league `LeagueFormat` fields
  (`keeper_ineligible_rounds`, `keeper_slot_rounds`, `keeper_slots`);
  draft-history keeper-cap math takes a configurable slot count.
- ✅ **Storage seam** (2026-08-10): `app/repository.py` —
  `get_repository(league_id)` serves rosters/draft history/standings/rankings
  for any registered league; web.py reads go through it, never direct paths.
  *Deviation from the original plan:* backends are JSON-file-backed for now;
  the SQLAlchemy/SQLite engine is deferred to Phase 1, where the first table
  with a real reason to exist (`users`) arrives. The interface is the
  contract — the DB becomes another backend behind it, call sites unchanged.
- Keep Flask, keep the CLI. Wrap the existing ~8k lines; don't rebuild them.
- ✅ **Pre-Phase-3-continuation cleanup** (2026-08-11): two small refactors,
  done ahead of porting draft board/mock draft/outcome log per-league so
  those ports don't repeat the same mistakes the keeper board's Phase 3
  slice 1 had to unwind.
  - `YahooRosterPlayer` moved out of `yahoo_client.py` into a new
    `app/roster_player.py` as platform-neutral `RosterPlayer` —
    `strategy.py`'s `league_keeper_board()` builds this type from ANY
    platform's repository dict (Yahoo/Sleeper/ESPN), so the Yahoo-branded
    name was misleading. `yahoo_client.YahooRosterPlayer` kept as an alias
    (`= RosterPlayer`) for the genuinely-Yahoo-only call sites
    (`roster_store.py`, `mcp_client.py`, `cli.py`) — no churn there.
  - Keeper-forecast business logic pulled out of `web.py` into new
    `app/keeper_service.py` (`keeper_board_state()`, `forecast_keeper_decisions()`,
    `calculate_keeper_impact()`, `load_keeper_marks()`, ADP enrichment
    helpers) — was living in the Flask route file, which would have kept
    growing every time a new per-league tool got ported. `web.py`: 1219 →
    863 lines. Route handlers now just call into the service module; no
    behavior change (smoke-tested against `/`, `/keepers-board`,
    `/league/<slug>/keepers`, and the `/keepers-board/mark` AJAX endpoint
    post-refactor, all pylint-10/10).
- Remaining in Phase 0: convert CLI analysis commands to the repository /
  `--league` flag where it makes sense (most CLI commands are single-league
  ingestion paths that Phase 2's API sync replaces anyway).

## Phase 1 — Sleeper-only multi-user MVP

The first version strangers can touch.

- ✅ **DB foundation** (2026-08-10): SQLAlchemy + SQLite (`app/db.py`,
  `data/wuff.db`, gitignored); `DATABASE_URL` env var is the Postgres path.
  Tables: `users`, `leagues`, `user_leagues` (`app/models.py`).
- ✅ **Accounts, dev transport** (2026-08-10): Flask-Login sessions with an
  email-only dev login (`app/auth.py`, `/login`). ⚠️ No verification — a
  real transport (magic-link email or Google via Authlib) MUST replace the
  dev form before any public deploy.
- ✅ **Onboarding** (2026-08-10): `/my/onboard` — enter Sleeper username →
  discover leagues via API → pick → import (DB rows, idempotent, leagues
  shared across users) + snapshot sync. `/my/leagues` lists the user's
  imported leagues.
- ✅ **Background sync + rate budget** (2026-08-10): APScheduler in-process
  (`app/sync_scheduler.py`) — periodic sweep of every known Sleeper league
  (DB-imported + local config, default every 6h via
  `SLEEPER_SYNC_INTERVAL_MINUTES`), onboarding/manual syncs run as one-off
  background jobs, every attempt recorded in the `sync_runs` table
  (surfaced on /my/leagues with a Sync-now button). Global sliding-window
  rate limiter (`app/rate_limit.py`) inside `sleeper_client._get` —
  `SLEEPER_MAX_CALLS_PER_MIN`, default 600 of Sleeper's 1000/min ceiling.
  Players cache auto-refreshes when older than 7 days.
- ✅ **Production facelift** (2026-08-10): site branded "wuff" (headline +
  tagline; no league name in the chrome), global nav reduced to
  Leagues / My leagues / Settings, league tools moved to a per-league
  subnav, /leagues hub grouped by provider. FantasyPros data fully removed
  in favor of the daily free-source rankings refresh. Keeper marking UI:
  logged-in users toggle a team's keepers on /keepers-board
  (`keeper_marks` table) and marked players drop off the draft board.
- ✅ **Real login transport** (2026-08-12): magic-link email replaces the
  dev email-only form. `app/mailer.py` (Resend) + `app/auth.py`
  (itsdangerous signed tokens, 15-min expiry, per-email 60s send cooldown).
  `/login` sends a link instead of logging in directly; `/login/verify/<token>`
  completes it. Without `RESEND_API_KEY` set, prints the link to the console
  instead of emailing it — lets the flow be built/tested with zero email
  infra, but means a missing/unshared env var fails *silently* (200 success
  page either way) rather than erroring, so "email never arrived" always
  means "check the server logs for the fallback line" first, not "debug
  Resend." Verified live end-to-end on Railway the same day: send → Resend
  delivers → click → session created → `/my/leagues`.
- ✅ **Deployed to Railway** (2026-08-12): live at
  wuff-production.up.railway.app. Gunicorn single-worker via `Procfile`
  (matches the scheduler decision above). Postgres plugin attached —
  Railway's filesystem is ephemeral, SQLite would lose all user data on
  every redeploy. `app/db.py` normalizes `postgres://` → `postgresql://`
  (Railway hands out the old scheme; SQLAlchemy 2.x's dialect lookup
  rejects it outright). ⚠️ Railway env vars — even ones marked "shared" to
  a service — do not hot-reload into an already-running container; changing
  one requires an explicit redeploy before the process sees it.
- ✅ **Hand-curated Yahoo data into the database** (2026-08-12): the Frank
  Gore league's draft history, pick ownership, standings and rosters lived
  only as JSON under the gitignored `data/raw/`, so the deploy had none of
  it — every page backed by them rendered empty, silently. Now
  `app/yahoo_models.py` / `app/yahoo_store.py` / `YahooDbRepository`, loaded
  by `python3 -m app migrate-yahoo-data`. `parse-rosters` writes the DB
  (JSON kept as a local working copy). Two paths that bypassed the
  repository were routed through it: `qb_historical_adjustment` (runs daily
  under the scheduler — would have silently dropped the QB adjustment in
  prod) and `outcome_log`'s legacy fallback.
  Gate was `scripts/compare_yahoo_backends.py`, which asserts the JSON and
  DB backends return **equal output for every method and every year** —
  CLAUDE.md's diff-the-full-output rule, since both prior bugs of this shape
  were silent. Mock draft, draft analysis, manager report and draft patterns
  were each diffed across backends too; all identical.
  **Not migrated, on purpose:** `rankings/yahoo_rankings.json` (rewritten
  daily by `refresh_free_rankings()`, self-heals like the Sleeper/ESPN
  snapshots), and `managers/` + `season_rosters/` (read only by
  `keeper_history.py`'s CLI commands, never by the web app — still
  local-only, migrate them if anything web-facing ever needs them).
- ✅ **The app requires login** (2026-08-12): every endpoint except
  `login`/`login_verify`/`logout`/`static` now goes through `_require_login`
  in `web.py`. Until then every page — the dashboard, keeper board, mock
  draft, draft history, per-league pages — served the default league's real
  rosters, standings and draft history to *anonymous* visitors, a leftover
  from when wuff was a single-user local tool. Deliberately an allowlist
  rather than ~30 `@login_required` decorators: a newly added route is then
  private by default, and forgetting a decorator would leak silently.
  (Manager names/emails were never exposed — they live in `managers/`,
  which is not migrated and which nothing web-facing reads.)
- ✅ **The default league is per user** (2026-08-12): `/`, `/keepers-board`,
  `/mock-draft`, `/standings`, `/draft-history`, `/draft-picks` and
  `/draft-order` used to resolve to `default_league_id()` — frank-gore — for
  every logged-in account. Login stopped strangers reading it; a second real
  user still landed on my league instead of their own. Now `app/membership.py`
  answers both questions: which leagues a user may see (a `user_leagues` row,
  matched on (platform, platform_league_id)) and which is theirs by default
  (`User.default_league_slug`, a slug rather than a `leagues.id` FK because the
  Yahoo league lives in the registry file and may have no DB row). No global
  fallback: a user who follows nothing has no default and is sent to
  onboarding, because falling back is the leak. Per-league routes and
  `/sleeper/<id>` + `/espn/<id>` redirect to `/leagues` for non-members;
  `/leagues` lists only the caller's leagues; `/my/leagues` gained a
  "Make default" button. `/keepers-board` and `/mock-draft` accept
  `?league=<slug>` so a Sleeper-default user can still open the Yahoo league
  they follow rather than bouncing between the two pages.
  Registry leagues (frank-gore and the local Sleeper entries) reach an account
  only through `python3 -m app grant-league --email X --league <slug>
  [--default]` — nothing imports them, so they otherwise have no follower and
  are visible to nobody. Deliberately not a web action: a claim button on a
  public deploy hands my league to the first stranger who clicks it.
  ⚠️ **Deploy step:** run `grant-league` against production once (with
  `DATABASE_URL` pointed at Railway's Postgres) or the owner account sees
  empty pages — it follows no league until granted.
  Gate was `scripts/check_league_scoping.py` (throwaway SQLite, real requests
  through the Flask test client, one user per scenario) plus a full-body diff
  of every Yahoo page before/after: byte-identical except the intended
  `data-league-slug`/hidden-input additions. Fixed one pre-existing 500 found
  on the way: `/sleeper/<id>` rendered `sleeper_league.html`, renamed to
  `league_snapshot.html` back when ESPN landed — an un-synced Sleeper league
  is now exactly where a Sleeper user's `/` sends them.
- **Remaining for Phase 1 launch:**
  - Per-user league *views* still lean on the shared snapshot files; fine
    while snapshots are keyed by platform league id, revisit at hosting.
  - `keeper_marks` stay per-league and shared between that league's users
    (board adjustments are per-user). Right for co-managers, wrong the day
    two users in one league disagree about a keeper — revisit when a real
    league has two accounts.
  - ✅ **Scheduler decision (2026-08-12):** in-process APScheduler assumes
    one process. Deploy with `gunicorn --workers 1` — N workers would each
    start their own scheduler and independently sweep every Sleeper league,
    burning `SLEEPER_MAX_CALLS_PER_MIN` N times over and racing on
    `sync_runs`/snapshot writes. Fine at current traffic; revisit (pin to
    one worker, or move the sweep to a `python -m app sync-sweep` cron
    entrypoint) only if load ever justifies more than one worker.

## Phase 2 — ESPN, then Yahoo importers

ESPN first (2026-08-10 reorder): Yahoo won't grant even read-only API access,
so it can't gate the second platform.

- ✅ **ESPN importer, beta** (2026-08-10): `app/espn_client.py` (unofficial
  JSON endpoints ESPN's own web app uses) + `app/espn_manager.py` (writes
  the same snapshot shapes as Sleeper, so the repository backend and the
  shared league-snapshot template needed no changes). Onboarding: league ID
  (+ season) for public leagues; private leagues take pasted `espn_s2`/`SWID`
  cookies, stored encrypted (`app/crypto.py` Fernet — set
  `WUFF_ENCRYPTION_KEY` in production). Background sweep is
  platform-aware and re-syncs ESPN leagues with the stored credentials.
  ⚠️ Unofficial API — expect seasonal breakage; validated against mocked
  payloads, needs a real ESPN league for live validation.
- **Yahoo (whenever approval lands):** 3-legged OAuth per user, tokens
  encrypted per-user in the DB (`cryptography` is already a dependency;
  `oauth_server.py` / `token_store.py` are the seeds). Requires an approved
  public app. The Yahoo API replaces `parse-rosters` paste entirely —
  rosters, draft results, and league settings come from the API. Until then
  the Frank Gore league keeps its current paste/manual flows.
- **Player identity crosswalk:** sleeper_id ↔ yahoo_id ↔ espn_id ↔
  name+team fuzzy match, as a first-class table. ~~This is sneaky-hard;
  budget real time for it.~~ **Re-scoped 2026-08-13 — see Phase 5 step 1.**
  The id↔id mapping is nearly free: the Sleeper players cache and the
  nflverse roster CSVs each already carry the full crosswalk and are both
  already on disk. The hard part is only the Yahoo side, which has no
  platform player id at all.
- All three importers emit the Phase 0 normalized model.

## Phase 3 — Port the analysis tools

- ✅ **Keeper board per league** (2026-08-10): `/league/<slug>/keepers` runs
  the keeper engine on ANY registered/imported league — repository rosters +
  free-source rankings + the league's own synced draft history, with rules
  from `/league/<slug>/settings` (keeper slots, ineligible rounds, keeper-slot
  rounds, consecutive-season cap; cap 0 = no cap for dynasty). Rules persist
  in `DbLeague.rules_json`, merged over the registry by
  `app/league_service.resolve_league()`. Keeper marks are league-scoped.
  `strategy.py` no longer reads global draft history — `draft_years` is
  threaded through. Live-verified on a real synced Sleeper league (12 teams,
  round-1/2 rule enforced against its actual 2026 draft).
- ✅ **Untangle before continuing the port** (2026-08-11): `RosterPlayer`
  de-Yahoo'd (see Phase 0) and keeper business logic moved out of `web.py`
  into `app/keeper_service.py` — both were named/placed for a single-league
  app and would have made the draft-board/mock-draft ports below repeat the
  same Yahoo-coupling cleanup keeper board already needed once.
- ✅ **Draft analysis per league** (2026-08-11): `draft_slot_vs_final_rank()`
  and `position_in_round_vs_final_rank()` take an optional `repo` and read
  that league's own draft history + standings through `app/repository.py`
  (omit it and you get the default league, so old callers are unchanged).
  CLI `draft-slot-outcomes` / `position-round-outcomes` gained `--league`,
  which is the flag Phase 0 listed as remaining work — use it as the pattern
  for the rest of the CLI analysis commands. Web: `/league/<slug>/draft-analysis`
  + per-league subnav entry, with an empty state for leagues that don't yet
  have a season with BOTH draft results and final standings (that's the real
  gate on this analysis, not platform).
- ✅ **Mock draft per league** (2026-08-11): `run_mock_draft(repo=, league_format=)`;
  team count, round count, keeper-slot rounds, starter slots and position
  limits all come from `LeagueFormat` (which gained `draft_rounds` +
  `total_draft_rounds`, inferred from `keeper_slot_rounds` when unset so
  existing configs don't change). `get_draft_order_2026{,_with_trades}()`
  collapsed into `build_draft_order(repo, league_format)`. Position limits
  are *derived* from the league's own starter slots rather than a fixed
  table — verified the derivation reproduces frank-gore's old table exactly.
  Web: `/league/<slug>/mock-draft`; both mock-draft pages share
  `_partials/mock_draft_results.html`, which reads rounds/pick numbers off
  the picks instead of hardcoding `range(1,16)`/`*12`/`>=14`.
  **Five real bugs fell out of diffing full simulator output against the
  pre-change version** — worth repeating that technique on any scoring or
  simulation change, since not one of them raised an error:
  1. traded picks silently ignored (parsed the raw file shape when
     `repo.draft_picks()` returns a normalized one);
  2. a `best_score = -1` floor made picks vanish entirely when every
     candidate scored below it (Yahoo's keeper rounds masked it; a Sleeper
     league produced 157 picks instead of 180);
  3. the simulator read `data/processed/rankings_adjusted.json` — a July
     snapshot from the superseded QB-knockback method — in preference to
     the live board, so it drafted off different rankings than the keeper
     board it takes its keepers from. Now `repo.rankings()`;
  4. ranking sources spell team defenses `DEF` while this module keys
     limits on `DST`, and an unrecognized position gets *no* limit — a
     12-team league drafted 15 defenses. Normalized at the boundary;
  5. the approaching-limit penalty fired at zero owned (`pos_count ==
     pos_limit - 1` is true at 0 when the limit is 1), so no team ever
     drafted its first defense.
- **Rankings sourcing (licensing-safe):**
  - (a) each user uploads their own CSV/PDF — current flow, zero legal risk,
    most friction; and
  - (b) free sources: Sleeper ADP, FantasyFootballCalculator ADP API,
    nflverse data.
  - Launch with a + b. A licensing deal (FantasyPros partnership) only if the
    product gets real traction.
- ✅ **Outcome log per league** (2026-08-11): every entry in
  `app/outcome_log.py` now carries `platform`/`platform_league_id` (same
  convention as `KeeperMark`); `resolve_outcomes()` groups pending entries
  by league and resolves each against that league's own `draft_years` via
  `app/repository.py`, instead of one global Yahoo-only resolution pass.
  Decision ids stay unscoped for the default Yahoo league (back-compat with
  pre-existing entries); other leagues get a `{platform}-{platform_league_id}_`
  prefix. The bigger gap this closed: the web keeper board never logged
  anything before — only the CLI `keepers-board-export`/`apply-qb-adjustment`
  paths wrote to the log. `app/keeper_service.py`'s `log_team_keeper_forecast()`
  now logs the touched team's current keepers after every `/keepers-board/mark`
  toggle, for whichever league the click was on. Still not done: nothing
  *reads* the log back to adjust scoring yet (see README's "What's next") —
  this slice is the write/resolve side generalized, not the learning loop
  itself.
- ✅ **Outcome log read side** (2026-08-11): `accuracy_report()` +
  `python3 -m app outcome-accuracy [--league <id>]` — groups resolved
  entries by league/decision_type/method_version and reports `hit_rate`
  (keeper_forecast) or `mean_delta`/`mean_abs_delta` (qb_adjustment).
  Deliberately stops at reporting: every logged forecast is still `pending`
  as of 2026-08-11 (no season has drafted this cycle), so there's zero
  resolved volume to safely tune a scoring weight against yet — the
  weight-adjustment step stays unbuilt until that changes, not because it's
  hard to code but because it can't be validated on no data.

- ✅ **Personal draft board** (2026-08-11): the data-derived board stays the
  base; each user layers their own opinion on top with ▲/▼ arrows per row
  (`app/board_service.py`, `BoardAdjustment`). Stored as a signed offset, not
  a pinned rank, so adjustments survive the daily refresh instead of freezing
  a stale board. Per-player reset and reset-all. Alongside it,
  `app/ranking_history.py` snapshots the board daily so rows show
  week-over-week movement — that data cannot be backfilled, which is why it
  shipped before the UI that consumes it.
  **Still open on this thread:** export a user's board, and saved/named
  versions they can compare. Also worth noting this is the first feature where
  a user has *personal work* to lose, which raises the stakes on replacing
  Phase 1's dev login stub.
- ✅ **Board adjustment UI rework** (2026-08-11): the ▲/▼ arrows previously
  showed unconditionally for any logged-in user; now gated behind a
  "Customize my board" toggle (`board-collapsed` CSS class on
  `#draft-board-table`, state kept in `localStorage` per league — a display
  preference, not user data) so the default view reads as data, not an edit
  surface. Added an always-visible ADP column to `draft_board_rows.html` (the
  raw market source, `row.adp`, was already computed but only surfaced via
  the Trend column's delta) so a pinned player still shows the number it was
  pinned away from. Closes the "not sure I like the feel" pause noted the same
  day — see the memory file, not restated here.

### Feature backlog (folded in from the earlier single-league roadmap)

These become *more* valuable in multi-league context; sequence them after the
core port:

- ✅ **Manager report card** (2026-08-12): `app/manager_report.py`,
  `/league/<slug>/manager-report`, CLI `manager-report --league <id>`. Per
  manager: `value_over_expected` = this league's own baseline avg final rank
  for the draft slots a manager actually drafted from (from
  `draft_analysis.py`'s `summarize_draft_slot_correlation`), minus their own
  actual avg final rank. Data-derived baseline only, no external assumption,
  no letter grades. Deliberately does NOT grade individual pick quality
  against season fantasy points — that's the ADP-vs-outcome "gem-finding"
  shape already rejected 2026-07-31; this stays one level up (finish vs.
  slot-expectation). ⚠️ **Cross-season identity is incomplete**: checked
  against real data before shipping — a 12-team league across 5 seasons
  produced 24 "manager" rows, not ~12, because most historical renames never
  got linked (only Yahoo's own rename note resolves identity, and it rarely
  fires; no persistent owner id is saved in the standings snapshots, and
  re-fetching one from Yahoo is blocked on OAuth approval). Shipped anyway,
  labeled honestly: every row carries `team_names` (every raw name folded
  into it) so the page shows its own limitation instead of overclaiming
  identity. A hand-authored alias file is the fix if this needs to be exact
  — not built speculatively.
- **Trade analyzer** — VOR impact of a proposed trade for both sides, reusing
  `strategy.py` scarcity logic.
- **Draft-day live mode** — keeper board / draft order updating as picks come
  in. Multi-user infra from Phase 1 makes this feasible for the first time.
- **Discord/Slack bot** — analysis logic as a library, posting to league
  chats. Good async-engagement hook once hosted.

## Phase 4 — Production hardening

- Postgres, gunicorn behind a proxy; host on Fly.io / Railway / Render
  (~$5–20/mo to start).
- Privacy page, terms of service, and account/data deletion — holding OAuth
  tokens and ESPN cookies means holding sensitive data.
- Basic error monitoring (Sentry free tier) — a demoable product can't 500
  silently.

## Phase 5 — Domain model (planned 2026-08-13)

Finishing Phase 0's platform-abstraction promise properly, now that Phases
0–3 have shown where it leaks. Full plan and rationale:
`WS-1-data-platform/Domain_Model_Refactor_2026-08-13.md` in the Obsidian
vault.

**The problem.** `app/repository.py` is a real seam with four backends, but
it serves **untyped dicts** — the normalized shape lives in a docstring and
is enforced by nothing, so Yahoo and Sleeper can disagree and no test, type
checker or runtime error will say so. Every silent-wrong-output bug logged
in this file is that shape. Only one domain dataclass exists in the whole
codebase (`RosterPlayer`); `LeagueFormat` is config, not a model.

**Two keys rot everything.** Player is a name string (ten independent
`normalize_name` implementations across strategy, board_service,
adp_manager, ranking_history, outcome_log, keeper_history,
rankings_aggregator, draft_history, rankings_manager). Franchise is a
display-name string (`KeeperMark.team_name`, standings `'team'`, draft-pick
`'team'`) — a rename orphans that data, which is exactly why
`manager_report.py` yields 24 managers for a 12-team league.

**The crosswalk is cheaper than this file previously claimed.** Phase 2 calls
player identity "sneaky-hard, budget real time for it." Checked against the
payloads wuff already downloads: `players_cache.json` carries `espn_id`,
`yahoo_id`, `gsis_id`, `sportradar_id` (plus `injury_status`/`status`) for
12,218 players, and `data/raw/nfl_stats/rosters/{year}.csv` carries
`gsis_id, espn_id, yahoo_id, sleeper_id, pfr_id, …`. Two independent
crosswalks, both already on disk, both free. The genuinely hard part is
narrower: **the Yahoo league has no platform player id at all** — its roster
rows set `playerId` to the player's name — so name matching is needed on the
Yahoo side only (12 teams × ~16 players + ~1,128 historical picks). Team
defenses never resolve (not in nflverse rosters); that gap is permanent and
must stay visible.

Steps 1–4 are refactor (no new data, net code removal); steps 5–8 are new
per-platform ingest, three times over — a different cost class, don't bundle.
Each step is its own commit with a full-output diff as the gate.

1. **Player identity registry** — `app/player_registry.py` + `players` table,
   built from the Sleeper cache + nflverse CSVs, one `resolve()` API. Gate:
   `scripts/check_player_resolution.py` prints resolved/unresolved per source;
   ship with the unresolved list visible, not forced to zero.
2. **1b. Collapse name normalization** onto `resolve()` — separate commit
   (step 1 adds, 1b removes). Gate: full-output diff of keeper board, mock
   draft, draft analysis, draft patterns, manager report.
3. **Franchise identity** — `franchises` table, stable id + name history.
   Sleeper's `roster_id`/`owner_id` are already synced and dropped at the
   repository boundary; Yahoo needs the hand-authored alias file
   `manager_report.py` already identified as the fix. Add `franchise_id`
   *alongside* `KeeperMark.team_name`, backfill, then switch reads — this is
   live user data, never a rename-and-pray.
4. **Typed domain layer** — `app/domain.py` frozen dataclasses (`Player`,
   `Franchise`, `Manager`, `RosterSlot`, `Roster`, `DraftPick`, `Draft`,
   `StandingRow`, `Transaction`, `PlayerStatus`, `Matchup`, `ScoringRules`).
   Repository grows typed methods *next to* the dict ones; consumers migrate
   one per commit; dict methods die when nothing calls them.
5. **Backend contract suite** — generalize `scripts/compare_yahoo_backends.py`
   into one parametrized suite over every backend × every league.
6. **Player statuses + bye weeks** — statuses are nearly free
   (`injury_status`/`status` are in the Sleeper cache and currently dropped by
   `_resolve_player()`; `RosterPlayer.status` is always `None`). Byes need a
   schedule source (nflverse schedules). First step that unlocks start/sit.
7. **Transactions** — real ingest work per platform. Sleeper
   `/league/{id}/transactions/{week}`; ESPN unofficial; Yahoo still blocked.
   Unlocks add/drop and the trade analyzer already in the backlog.
8. **Matchups + scoring engine** — something that finally reads
   `LeagueFormat.scoring`. Then **playoffs** (bracket, seeding, odds) last.

**Out of scope, deliberately:** modeling all 16 entities up front (model to
the decisions the product serves — keep, draft, start/sit, add/drop, then
stop); rewriting the CLI or templates (Phase 0's wrap-don't-rebuild rule
stands); re-deriving manager identity algorithmically (already tried, already
failed against real data).

**If only one step happens, it's step 1** — statuses, byes, stats joins and
transactions all need a player key before they can exist.

---

## Risks, in order

1. **Platform API access.** Yahoo has proven it: still no read-only access
   after ~3 weeks (hence deprioritized). ESPN's unofficial endpoints can
   break any season. Sleeper is the only platform wuff fully controls its
   own destiny on — which is why it's the MVP and the deepest integration.
2. **Rules diversity.** Every league has weird keeper rules. The rules engine
   either handles "config, not code" or the project drowns in special cases.
3. **Player identity matching** across three platforms' ID spaces —
   downgraded 2026-08-13 (Phase 5 step 1): two free crosswalks already on
   disk cover Sleeper/ESPN/nflverse. The residual risk is Yahoo-only name
   matching, plus team defenses, which never resolve.
4. **Rankings licensing** — solved at launch by user-upload + free sources,
   but caps how "turnkey" onboarding feels.

## Explicitly out of scope

- ML feature table / gem-finding (removed 2026-07-31; stays removed).
- Selenium/browser scraping (removed 2026-07-31; API + paste only).
- React/SPA frontend rewrite.
- Monetization.
