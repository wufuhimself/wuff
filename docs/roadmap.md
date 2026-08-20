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
  - ✅ **Moved to `python3 -m app sync-sweep` (2026-08-17):** the alternative
    named above, taken not because traffic justified it but because a
    future Airflow-driven schedule (for rankings/ADP refresh in particular)
    needs a callable entrypoint outside the web process anyway — an
    external scheduler is the actual target shape, and `workers=1` staying
    pinned no longer matters for the reason it used to. `sync_scheduler.py`
    keeps every job function (`sync_one_league`, `sync_all_due`,
    `refresh_rankings_job`, `refresh_nfl_rosters_job`,
    `refresh_schedule_job`, `refresh_player_registry_job`) — only
    `ensure_scheduler_started()` changed, and only by not registering
    periodic jobs anymore. It still starts a `BackgroundScheduler` for
    `queue_league_sync()`'s one-off jobs (onboarding import, the "Sync now"
    button), which are unaffected and were verified still working after the
    change. `sync-sweep` runs all 5 jobs in the same dependency order the
    scheduler used to (rosters → registry → rankings → schedule → league
    sync), prints per-league sync results, and exits non-zero if any
    league's `SyncRun` recorded `error` — so an external scheduler can alert
    on a real failure (verified against a live 404 from Sleeper on one
    already-defunct league in the local config, not a bug this change
    introduced).
    `railway.sync-sweep.json` (committed, not the default `railway.json` —
    naming it separately means it only applies to whichever service is
    told to use it, not the existing `web` service) declares
    `startCommand: python3 -m app sync-sweep` and `cronSchedule: 0 */6 * * *`
    (every 6h, matching the old `SLEEPER_SYNC_INTERVAL_MINUTES` default —
    the daily jobs just run more often than strictly needed, which is
    cheap and harmless; not worth a second cron service to split the
    cadence until there's a reason to).
    ⚠️ **Deploy step still open, manual, one-time, in the Railway
    dashboard** (this part cannot be done from the repo): create a **new
    service** in the same Railway project, pointed at this same GitHub
    repo, with no domain/port needed. In that service's Settings →
    Config-as-code, set the path to `railway.sync-sweep.json`. Set the
    same `DATABASE_URL` (and any other env vars `sync-sweep`'s jobs need —
    it shares `app/db.py`'s Postgres connection with `web`) on the new
    service; Railway env vars don't cross services automatically. Confirm
    the Cron Schedule shows under that service's Settings once the config
    file is picked up. It must stay a **separate** service from `web` —
    cron services must exit on completion, and folding this into the
    always-running web service would silently stop every job the old
    in-process scheduler ran. Does **not** require a Dockerfile — Railway's
    cron scheduling works the same under Nixpacks — so containerizing the
    app and adding the cron service are independent decisions, only
    related in that both came up the same day. Until that service exists,
    rankings/ADP/nfl-rosters/player-registry/schedule/league-sync all stay
    frozen at whatever `sync-sweep` was last run manually.

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
  gate on this analysis, not platform). **Web page removed 2026-08-20** ("not
  great") — route, template, and both nav links deleted. `draft_analysis.py`
  itself stays: `manager_report.py` is built on `draft_slot_vs_final_rank()`/
  `summarize_draft_slot_correlation()`, and the CLI commands
  (`draft-slot-outcomes`/`position-round-outcomes --league`) are untouched.
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

1. ✅ **Player identity registry** (2026-08-13): `app/player_registry.py`
   (build + resolve) + `app/player_store.py` (persistence + process cache) +
   `app/player_models.py` (`players`, `player_aliases`). CLI
   `build-player-registry`; daily `player-registry-daily` scheduler job.
   Canonical ids are `sleeper:{id}` first — **not** gsis-first as originally
   planned, because a rookie can reach Sleeper before nflverse assigns a gsis
   id, which would silently change that player's key on the next rebuild.
   Resolution never guesses: an ambiguous name returns None, and an explicit
   non-fantasy position that matches nothing returns unknown rather than
   falling through to a fantasy player. Coverage across every registered
   league: rosters 711/711, rankings 3885/3892, draft history 1643/1648; the
   five stragglers are genuinely unresolvable (Sleeper stores both Frank
   Gores as "Frank Gore", Ronald Jones appears twice identically, Will Fuller
   V is in neither source). `data/config/player_aliases.json` is the
   hand-authored escape hatch for nicknames and short forms.
   Two silent bugs the gate caught: duplicate `nfl:` identities for players
   whose Sleeper row has no gsis id (Jake Bates, Cade York, Mike Washington),
   and team defenses matching none of the four spellings sources use.
   **Correction to Phase 2's note above:** DST *does* resolve — "not in
   nflverse" is true, but the Sleeper cache carries all 32 as `DEF` rows.
   Gate: `scripts/check_player_resolution.py`, which asserts per-source
   floors rather than zero unresolved — forcing zero would mean guessing.
2. ✅ **1b. Collapse name normalization** (2026-08-13): the ten normalizers,
   plus a dozen inline `.lower().strip()` keys the count had missed
   (mock_draft alone had seven), now share
   `player_registry.normalize_name`. Two stay separate *on purpose* and say
   so in their docstrings — `outcome_log`'s `decision_id` slug and
   `rankings_manager.normalize_player_id` generate **persisted** keys, so
   changing the format orphans rows rather than improving a lookup.
   `nfl_position_map.json` was regenerated in the same commit: its keys come
   from the same normalizer and are read back by `draft_patterns` and
   `qb_historical_adjustment`, so those three had to move together.
   **The full-output diff earned its keep again**, catching three things that
   raised no error: (a) the live rankings board carries the same player twice
   under two spellings ("Aaron Jones Sr." at 93 *and* "Aaron Jones" at 268),
   and once a shared key collapses them every naive
   `{normalize(name): row}` comprehension kept the *worse* duplicate — the
   same last-row-wins collision `fantasy_position_map` documents; (b) those
   duplicates meant the mock draft was drafting the same human twice and the
   trend column showed a confident "down 237" for a player who had not moved;
   (c) the first cut of the fix reintroduced the bug one layer up. New
   `index_rows_by_name()` (better-ranked row wins, suffix-preserving key
   indexed too) and `dedupe_rows_by_name()` at the repository seam.
   Verified improvements: Aaron Jones RB71→RB34, Brian Thomas WR92→WR39,
   Harold Fannin TE26→TE6, Kenneth Walker III unranked→RB14; draft-history
   position resolution 541→577 picks, completing rounds 2 and 3. No player
   added to or dropped from any keeper board.
3. ✅ **Franchise identity** (2026-08-13): `app/franchise_registry.py` +
   `franchise_store.py` + `franchise_models.py` (`franchises`,
   `franchise_names`), CLI `build-franchises` and
   `franchise-alias-template`. Sleeper/ESPN resolve from `ownerId` (fallback
   `rosterId`) — the data was already synced and being thrown away at the
   repository boundary. **Yahoo turned out to be resolvable too** — not from
   the standings (no owner id; the rename note fired for 1 of ~47 name
   changes) but from `data/raw/managers/{year}.json`, which carries one row
   per manager per season **with their email**, and whose team names match the
   standings names 12/12 in every saved season. Both this file and
   `manager_report.py` had recorded that no persistent owner id was saved
   anywhere and that hand-authoring was the only fix; it existed all along in
   a file nothing web-facing read. `franchise-alias-template --from-managers
   --write` generates `data/config/franchise_aliases.json` from it:
   frank-gore resolves to **15 managers instead of 48 name-lineages**, and
   `manager_report` drops 24 rows → 14 (14 not 15 — one manager has no season
   with both a draft and saved standings). The archive is gitignored and
   local-only, so this is a *generator* for the committed file, same shape as
   `snapshot-position-map`; the output carries team names and a slug only,
   never an email or a real name. The file stays hand-editable for leagues
   with no such archive. The only automatic name links are *exact* ones —
   identical slugs, and a roster name whose tail after `" - "` matches a
   known team. `KeeperMark.franchise_id` was added **alongside** `team_name`,
   backfilled by `build-franchises`, with NULL falling back to name matching.
   **Two invisible data problems surfaced:** the Yahoo roster paste prefixes
   every team with the league name, so rosters and standings had never been
   joinable on team at all; and a rename *target* only exists inside the note
   text, so "Wuf" was not a known name and the one genuinely renamed team had
   marks pointing at a franchise that did not exist. The before/after diff
   also caught my own first cut using the prefixed roster name as the display
   name, which silently stopped every frank-gore keeper mark from matching.
   Result: all 7 leagues byte-identical except the intended
   `manager_report` 24→23 rows (the one provable merge). It stays 23 rather
   than 12 until the alias file is filled in by someone who knows the
   managers. Gate: `scripts/check_franchise_identity.py` renames a team on
   each platform's own terms and asserts the marks follow.
4. ✅ **Typed domain layer** (2026-08-13): `app/domain.py` —
   `RosterTeam`/`RosterEntry`/`DraftPick`/`StandingRow`/`RankingRow`, each
   carrying `canonical_player_id` and `franchise_id` from steps 1–2. Scoped
   to what the repository actually serves today; `Transaction`/`Matchup`/
   `ScoringRules` wait for steps 6–7 rather than being modelled speculatively.
   Typed methods are implemented **once on the base class** in terms of the
   dict methods, so a backend cannot drift from the contract by forgetting a
   field — which is precisely how the dict version failed. Sleeper's missing
   `rank` is filled from sort position; `rosterId`/`teamId` unify as
   `platform_team_id`. `raw` keeps the original dict as a migration aid.
   Purely additive: full-output diff byte-identical.
   **First consumer migrated** (`draft_patterns`, own commit): position now
   resolves from the season's roster snapshot first (season-accurate) then the
   player registry. frank-gore goes 541 → 980 resolved picks of 1128, 4 → 6
   seasons, and **DST resolves for the first time** (64 picks — nflverse has
   no team defenses, Sleeper has all 32). Zero picks fall through to OTHER.
   ✅ **Both follow-ups closed same day.** `earliest_rounds_for(league_format,
   repo=None)` now prefers `position_timing()`'s real `first_round` per
   position once a league has ≥`MIN_TIMING_SAMPLES` (20) picks there, else
   the old fraction heuristic — every Sleeper league here still uses the
   heuristic (0–8 samples after one season); frank-gore's floor moved 9→7
   (DST) and 12→10 (K), though checked and confirmed **not the binding
   constraint** in its simulated draft (DST already lands rounds 9–10 on
   scoring/scarcity alone; this league's format has zero K starter slots, so
   K is never drafted regardless). `qb_historical_adjustment._is_qb()` gained
   the identical snapshot-first/registry-fallback resolution: 2020–2021 now
   contribute (16 and 26 QB picks, verified pick-by-pick, e.g. 2021's
   Patrick Mahomes at 1.1), moving QB1's historical target pick **2 → 6**.
   That one feeds the *daily* rankings board rather than a page render, so it
   got its own commit and its own sanity check on the resulting board (top
   10: Gibbs/Robinson/Nacua/Chase/Allen) before trusting the number.
5. ✅ **Backend contract suite** (2026-08-13):
   `scripts/check_repository_contract.py` — 12,941 assertions over every
   backend × every league: one typed record per dict record, names preserved,
   positions from a known set, every standing row with a unique rank. Found
   two real things on first run: a Sleeper team literally named
   `" Griddy - ators "` (whose franchise lookup the typed layer's trimming
   then broke — `FranchiseRegistry` lookups are whitespace-insensitive now
   while `Franchise.name` keeps the exact spelling the keeper board keys on),
   and dataclasses rejecting a bare `{}` default.
6. ✅ **Player statuses + bye weeks** (2026-08-13) — first step that unlocks
   a new product decision (start/sit) rather than fixing an existing one.
   Status/`injury_status` were already sitting unused on `PlayerIdentity`
   since step 1; `RosterEntry` now reads them off the resolved identity
   instead of the platform's own roster row, which no backend actually
   populates. 100% coverage across every league's resolved non-DEF players.
   New `app/bye_weeks.py`: byes are not a field anywhere upstream, they're
   **derived** — a team's bye is the one regular-season week nflverse's
   schedule has no game for. Checked against 2020–2026 before trusting it:
   every team has exactly one such week every season except 2022, where
   BUF/CIN each show two because their week-17 game was cancelled outright
   after the Damar Hamlin incident rather than rescheduled — correctly
   reported as no-bye-resolved rather than guessed. One real bug caught
   before shipping: nflverse spells the Rams `LA`, `player_registry`
   resolves rosters to `LAR` — a raw key would have silently given every Rams
   player a `None` bye forever; fixed by running every team code through
   `normalize_team()` on both sides. `data/config/nfl_bye_weeks.json` is the
   same self-healing committed-snapshot pattern as the position map, with the
   matching daily scheduler job (`nfl-schedule-daily`) — the exact "no copy
   on a deployed container" bug class this file already documents once.
   CLI: `fetch-schedules`, `snapshot-byes`. Gate: `check_repository_contract`
   extended to 14,425 checks (recognized-value sets, valid week range,
   per-league coverage reporting).
7. ✅ **Transactions** (2026-08-13) — Sleeper only, since that's the only
   platform actually buildable right now (ESPN has no transaction endpoint
   wrapped; Yahoo still blocked). `app/sleeper_client.get_league_transactions()`
   wraps `/league/{id}/transactions/{week}`, walking weeks 0–18 every sync
   since Sleeper exposes no "current week" field and an empty response marks
   "hasn't happened" cheaply enough not to need one. Wired into `sync_league()`,
   so every existing sync path (scheduled + manual) picks it up automatically.
   `app/domain.py` gains `Transaction`/`TransactionMove`/`TransactionPickMove`
   — a trade with 2 players is 4 `TransactionMove`s (add+drop per side), one
   shape covering trades/waivers/free-agent adds without a null-heavy
   trade-only variant. `repository.transactions()` resolves player moves via
   `by_platform_id('sleeper', ...)` (exact, no name matching needed) and
   attributes both player moves and traded picks by **roster_id, not team
   name** — a mid-season rename would otherwise mis-attribute an older
   transaction to the team's current name. Verified against all 6 real
   Sleeper leagues (17 transactions: 4 free_agent, 8 waiver, 6 trade,
   including a picks-only trade and a same-franchise add+drop waiver) — 100%
   player-move resolution everywhere real moves exist. Gate:
   `check_repository_contract` extended to 14,520 checks. Purely additive.
8. ✅ **Matchups** (2026-08-13) — Sleeper only (same scoping as step 6).
   **Scope correction, decided with the user before building:** this does
   *not* end up being "something that finally reads `LeagueFormat.scoring`."
   Sleeper's real `scoring_settings` run to ~130 rule keys (IDP tackles,
   special-teams yards, per-bracket defense bonuses) against `LeagueFormat`'s
   9 — recomputing from raw stats would silently diverge from what the
   league itself sees on any unmodeled rule. `MatchupSide.points` is
   Sleeper's own computed total, trusted directly; a real scoring engine
   stays future work, and only if `LeagueFormat.scoring` needs modeling for
   something like a points *projection* — a different problem.
   `sync_matchups()` bounds its fetch by `settings.leg` (Sleeper's own
   current/last-played-week field, confirmed correct on a completed season
   too — 17 weeks, `leg=17`, not reset) rather than walking a fixed range the
   way transactions does, because an unplayed matchups week isn't an empty
   response the way an unplayed transactions week is — it's real rows with
   every score at 0.0. **This season only**, confirmed with the user first:
   Sleeper chains history across seasons via `previous_league_id` under a
   *different* league_id, deliberately not walked. `repository.matchups()`
   drops any week where every side is still 0.0 — documented as a heuristic,
   not a guaranteed signal, and checked against a real completed 2025 season
   (reached via the chain, on demand, not registered) before trusting it: 82
   matchups across 17 weeks, 100% franchise resolution, 1639/1640 starters
   resolved (the one gap is Sleeper's own `'0'` empty-slot placeholder,
   already filtered elsewhere in this codebase — not a bug). Gate:
   `check_repository_contract` extended to 14,528 checks, including a
   dedicated completed-season check since every *registered* league's own
   matchup assertions currently pass vacuously on zero rows (none have
   played a game yet).
9. ✅ **Playoffs** (2026-08-13) — bracket structure, Sleeper only (same
   scoping as steps 6–7). `app/sleeper_client`'s bracket wrappers had already
   existed, unused; `sync_playoffs()` writes both winners/losers brackets,
   wired into `sync_league()`. Sleeper generates bracket **structure** the
   moment `playoff_teams` is configured — long before `playoff_week_start` —
   with `w`/`l`/`t1`/`t2` null until each match is actually played, so a
   not-yet-decided bracket is a real, useful state, not an error to wait out.
   New `PlayoffMatch`/`BracketSource` in `app/domain.py` deliberately do
   *not* duplicate points or team names — only `franchise_id` — since a
   playoff week's games are also ordinary weekly `Matchup`s already captured
   by step 7; `PlayoffMatch` carries only the structure a `Matchup` can't
   express. No week number either: Sleeper's payload has none, and inferring
   one from `playoff_week_start + round` would silently break for a bye
   round or a non-default bracket length. **One real bug caught before
   shipping, from testing against actual data rather than trusting the
   shape**: Sleeper's `t1_from`/`t2_from` resolve *independently* per side —
   this league's own real championship match has home coming from "winner of
   match 3" and away from "winner of match 4" simultaneously. A first cut
   with one match-level `advances_from_winner_of`/`advances_from_loser_of`
   pair and an `or` chain silently dropped the second reference; fixed with
   a proper per-side `home_from`/`away_from`, each an independent
   `BracketSource`. Verified both directions: an in-progress league's
   pre-generated bracket (round 1 seeded, later rounds correctly `None`
   pending results) and a real completed season (11/11 matches decided,
   championship winner cross-checked by hand against raw roster/owner ids
   before trusting the typed output). Gate: `check_repository_contract`
   extended to 14,572 checks — recognized bracket type, a bracket-source
   reference must point at a match that actually exists in the same
   bracket, a decided match's winner must be one of its own two sides and
   differ from the loser; completed-season sample now also asserts exactly
   one championship match with everything decided. Purely additive.

**This closes every step Phase 5's plan named (1–9).** At the time, nothing
outside `repository.py` read `Matchup`/`PlayoffMatch`/`Transaction` — the web
pages, CLI reports, and outcome-log integration that would surface this data
to a user were deliberately out of the refactor's scope, not a gap in it.

- ✅ **First consumer, same day** (2026-08-13): `/league/<slug>/matchups` —
  weekly head-to-head scores + the winners/losers bracket, read-only. Follows
  the established per-league route pattern exactly (`_member_league`,
  `_league_page_ctx`, `repository_for(league)`); nothing new invented at the
  web layer. Sleeper-only in practice (Yahoo/ESPN correctly render the
  existing empty state). `PlayoffMatch`'s deliberate franchise-id-only shape
  meant the view resolves team names **once**, against the league's own
  `FranchiseRegistry`, rather than teaching the template a lookup. Smoke-
  tested through the real Flask app against real synced data (not just a
  template render): a completed season (every week renders, championship
  labeled, zero unresolved bracket cells) and a pre-season league (round 1
  seeded, later rounds correctly show "TBD" pending results) plus access
  control (anonymous and non-follower both redirect). Two of the smoke
  test's own assertions were briefly wrong while writing it — caught before
  trusting the result, not shipped: one matched the literal string "TBD"
  against the page's *explanatory caption* rather than real bracket cells;
  the other looked for "Winners bracket" and found nothing because the
  heading renders lowercase with a CSS `text-transform`, not literal
  capitalized text in the HTML. Full-output diff across all 7 leagues'
  existing pages: byte-identical.

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

## Phase 6 — Agent runtime (LangGraph spike, started 2026-08-19)

Separate thread from Phase 5's domain-model refactor: an LLM reasoning layer
over the outcome log, not new data ingestion. Plan:
`WS-6-agent-runtime/LangGraph_Prototype_Plan_2026-08-19.md` in the Obsidian
vault.

- ✅ **Spike → wired into web app** (2026-08-19): `scripts/langgraph_spike.py`
  (step 1) proved out enough to move into `app/agent_reasoning.py` (step 2).
  `ask(league, question, thread_id)` is the single entry point, same
  factored-out-of-`web.py` shape as `keeper_service.py`. No vectorstore — a
  league's outcome log is a few dozen entries at most, so the whole thing
  (current forecasts + full change history, scoped via
  platform/platform_league_id) goes straight into the prompt; revisit only if
  a league's log gets big enough to stop fitting a context window. LLM is
  **local Ollama (`llama3.1:8b`)**, not the Anthropic API — cost, not a
  technical constraint; swap point is the single `ChatOllama(...)` call.
  New deps landed in `requirements.txt`.
- ✅ **Persistence fixed same night**: first cut used `SqliteSaver` only —
  looked correct locally, but `data/processed/` is gitignored and Railway's
  filesystem is ephemeral, so every redeploy silently wiped every user's
  conversation history. Same bug class as the Sleeper/ESPN snapshot fix
  (`app/snapshot_store.py`); `outcome_log.json` was the last one still open,
  closed 2026-08-20 (below).
  Caught by the user asking "is this actually persisted?" after using the
  live feature, not by testing. Now `PostgresSaver` against `DATABASE_URL`
  when it points at Postgres (production), `SqliteSaver` at
  `data/processed/agent_checkpoints.db` otherwise (local dev). `thread_id` is
  `f"{user_id}_{platform}_{platform_league_id}"` — per user, per league,
  users can't see each other's threads.
- ✅ **Renamed Ask → Scouting, rate-limited** (2026-08-19): route
  `/league/<slug>/ask` → `/league/<slug>/scouting` (`league_ask` →
  `league_scouting`, nav label, page title/heading). Internal `ask()` /
  `AskInProgress` names in `agent_reasoning.py` unchanged — they describe the
  mechanism, not the product-facing name. `QUESTIONS_PER_HOUR_LIMIT = 3`,
  sliding window (lifts exactly one hour after the oldest of the last 3
  questions, not top-of-clock-hour), read off the checkpointed messages
  list's `asked_at` field rather than a separate table — that list is already
  the durable per-turn record, so the limit needs no persistence of its own.
  Checked before the in-flight lock so a rate-limited call never contends for
  it. Page shows N-of-3-left and disables the form at zero. Verified against
  the real running app: old route 404s, 3 real questions fire exactly 3
  Ollama calls, 4th is rejected server-side (still exactly 3 calls, still
  exactly 3 turns in the transcript).
- ✅ **Outcome log moved to the database** (2026-08-20): the last unfixed
  instance of the bug class this file already records twice — the Sleeper/ESPN
  snapshots (`app/snapshot_store.py`) and the Scouting checkpoints above.
  `data/processed/outcome_log.json` and `outcome_log_history.json` are
  gitignored and Railway's filesystem is ephemeral, so every forecast the
  *deployed* app logged was wiped by the next redeploy, silently — and
  Scouting reasons over exactly this data, so on production the agent has been
  reading a log that resets. Worse than the snapshot case: a snapshot re-syncs
  from the platform's API on the next sweep, a forecast history cannot be
  re-derived from anything. Now `app/outcome_models.py` +
  `app/outcome_store.py`; `outcome_log.py`'s four storage functions swapped
  bodies and every caller (`keeper_service`, `cli`, `agent_reasoning`,
  `scripts/langgraph_spike`) is untouched. Deliberately **not** a
  DB-in-production/files-locally split — that leaves two write paths to keep
  in agreement, and every other piece of user state already lives in the local
  SQLite database; the JSON files are migration input only
  (`python3 -m app migrate-outcome-log`, run once per database from a machine
  that still HAS them — the deployed container never did).
  Two design points worth keeping: `OutcomeEntry.seq` exists because insertion
  order cannot be derived from `forecasted_at` (an upsert rewrites it, which
  would silently reshuffle the list every time a pending forecast changed);
  and the 4 pre-2026-08-11 entries that predate per-league scoping are
  normalized to the documented Yahoo default on the way in rather than given
  nullable columns, verified to keep their exact `decision_id`s.
  **One real pre-existing bug fell out of the gate**: `log_outcome()`'s upsert
  path returned early without ever calling `save_outcomes()`, so a call that
  owned its list and *changed* a forecast appended the history row recording
  the change and then discarded the change itself. Unreachable in production
  (every caller batches via `outcomes=` and saves explicitly), which is why it
  had never surfaced; fixed rather than left as a trap.
  Gate: `scripts/compare_outcome_backends.py` — throwaway SQLite, the real
  local 40-entry log as reference, and unlike `compare_yahoo_backends.py` it
  exercises the **write** paths too (upsert, identical re-log appends nothing,
  new entry appends last, `resolve_outcomes` in-place rewrite), since those
  are where the backends can actually diverge. Also smoke-tested through the
  real Flask app: a `/keepers-board/mark` click that genuinely changes a
  team's keepers updates the forecast row and appends one history row.
  `outcome-accuracy` output is byte-identical to the pre-change version.
  **Deploy step, optional:** `migrate-outcome-log` with `DATABASE_URL` pointed
  at Railway's Postgres carries the local log's existing entries over.
  Deliberately not run (2026-08-20) — nothing is really live yet, so the ~40
  local forecasts aren't worth preserving; new forecasts persist either way.
  The command stays for whenever there's history worth moving.
- **Open:** nothing yet reads `accuracy_report()` (Phase 3's outcome log) back
  into Scouting's prompt or reasoning — the agent currently reasons over raw
  forecast/outcome entries, not the aggregated hit-rate numbers. Also open:
  Ollama is a local/dev dependency — no plan yet for what serves inference in
  production (self-hosted, or swap to a hosted model once cost is worth
  revisiting).

## Explicitly out of scope

- ML feature table / gem-finding (removed 2026-07-31; stays removed).
- Selenium/browser scraping (removed 2026-07-31; API + paste only).
- React/SPA frontend rewrite.
- Monetization.
