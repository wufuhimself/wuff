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
- **Remaining for Phase 1 launch:**
  - Real login transport (magic link / Google) — the dev form is a stub.
  - Per-user league *views* still lean on the shared snapshot files; fine
    while snapshots are keyed by platform league id, revisit at hosting.
  - One process = one scheduler; under gunicorn multi-worker, pin the
    scheduler to a single worker or move the sweep to a cron entrypoint.

## Phase 2 — ESPN, then Yahoo importers

ESPN first (2026-08-10 reorder): Yahoo won't grant even read-only API access,
so it can't gate the second platform.

- **ESPN:** no official API. Public leagues via the free JSON endpoints;
  private leagues require the user to paste `espn_s2` + `SWID` cookies.
  Fragile and a ToS gray zone — ship labeled **beta**, expect breakage each
  season. Use the community `espn-api` library's endpoint knowledge as
  reference.
- **Yahoo (whenever approval lands):** 3-legged OAuth per user, tokens
  encrypted per-user in the DB (`cryptography` is already a dependency;
  `oauth_server.py` / `token_store.py` are the seeds). Requires an approved
  public app. The Yahoo API replaces `parse-rosters` paste entirely —
  rosters, draft results, and league settings come from the API. Until then
  the Frank Gore league keeps its current paste/manual flows.
- **Player identity crosswalk:** sleeper_id ↔ yahoo_id ↔ espn_id ↔
  name+team fuzzy match, as a first-class table. This is sneaky-hard;
  budget real time for it.
- All three importers emit the Phase 0 normalized model.

## Phase 3 — Port the analysis tools

- **Keeper board per league:** rules come from the rules engine. Auto-detect
  what the platform API exposes (roster slots, keeper counts); a league
  settings UI covers what platforms don't expose (round penalties,
  consecutive-year caps). The dynasty Sleeper league (no round-based cap) is
  test case #2 after Frank Gore.
- **Draft board, mock draft, draft analysis:** port after keeper; they're
  already mostly league-shaped.
- **Rankings sourcing (licensing-safe):**
  - (a) each user uploads their own CSV/PDF — current flow, zero legal risk,
    most friction; and
  - (b) free sources: Sleeper ADP, FantasyFootballCalculator ADP API,
    nflverse data.
  - Launch with a + b. A licensing deal (FantasyPros partnership) only if the
    product gets real traction.
- **Outcome log per league:** forecast-vs-actual tracking
  (`app/outcome_log.py`) generalized per league. This is the differentiator —
  "was the keeper advice right last year" is a story no dashboard product
  tells.

### Feature backlog (folded in from the earlier single-league roadmap)

These become *more* valuable in multi-league context; sequence them after the
core port:

- **Manager report card** — grade each manager's keepers/picks against actual
  outcomes. Skeleton exists in `app/draft_analysis.py`
  (draft_slot_vs_final_rank). High demo value: tells stories about a league.
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

---

## Risks, in order

1. **Platform API access.** Yahoo has proven it: still no read-only access
   after ~3 weeks (hence deprioritized). ESPN's unofficial endpoints can
   break any season. Sleeper is the only platform wuff fully controls its
   own destiny on — which is why it's the MVP and the deepest integration.
2. **Rules diversity.** Every league has weird keeper rules. The rules engine
   either handles "config, not code" or the project drowns in special cases.
3. **Player identity matching** across three platforms' ID spaces.
4. **Rankings licensing** — solved at launch by user-upload + free sources,
   but caps how "turnkey" onboarding feels.

## Explicitly out of scope

- ML feature table / gem-finding (removed 2026-07-31; stays removed).
- Selenium/browser scraping (removed 2026-07-31; API + paste only).
- React/SPA frontend rewrite.
- Monetization.
