# Hosting Plan: Sharing wuff with your league

Overview of what it takes to move wuff from a local CLI/web app to a
shared site your league mates can visit. (Non-commercial, just for fun.)

## Key constraint

wuff currently assumes:
- **One user** — you, with your Yahoo OAuth token
- **One token location** — `YAHOO_TOKEN_FILE` on disk
- **File-based storage** — JSON/CSV files under `data/raw/` and
  `data/processed/`
- **Local CLI as the ingestion path** — you run `python -m app parse-rosters`,
  `python -m app keepers-board`, etc. and they write files

This design works great for solo use. Hosting requires a decision about
what teammates can do, then a data/hosting layer swap.

## Phase 0: Sharing model — the key choice

You must pick one model; it determines the effort required in all
downstream phases.

### Option A: Read-only shared views (recommended)
- **What teammates see:** The computed keepers board, draft history,
  standings, gem analysis — all read-only pages
- **Who runs the ingestion:** You, locally or on a schedule (via a cron job
  or GitHub Action)
- **Teammate interaction:** None — they just visit the site
- **Auth:** No per-user login needed; a single shared password or
  unguessable URL slug is enough for privacy

**Tradeoffs:**
- ✅ Minimal code change — reuses 95% of your current Flask app and CLI
- ✅ No multi-user state to manage — teammates can't interfere with each
  other
- ✅ No per-user Yahoo token storage — you handle OAuth once, teammates
  don't need to
- ❌ If rosters/rankings need updating mid-season, you're the only one who
  can trigger it
- ❌ No live draft mode during the draft (you'd have to re-run CLI and
  refresh the page)

**Effort: ~2 phases (DB + hosting), ~2-3 weeks of part-time work**

### Option B: Multi-user interactive app
- **What teammates see:** The same read-only pages, but also controls to
  import their own rankings, refresh their own roster, change league settings
- **Who runs the ingestion:** Anyone can trigger it (you + teammates)
- **Teammate interaction:** Full — teammates log in with Yahoo OAuth, see
  their own computed outputs
- **Auth:** Per-user login (Yahoo OAuth), per-user token storage in the DB

**Tradeoffs:**
- ✅ Teammates have autonomy — no bottleneck waiting for you to refresh
- ✅ More engaging — people can experiment with "what if I kept X?" live
- ❌ Significantly more complex — multi-user token storage, per-user state,
  concurrency issues if two people refresh rosters at once
- ❌ The half-built OAuth flow (`app/oauth_server.py`, `app/token_store.py`)
  needs to scale to per-user tokens in the DB

**Effort: ~4 phases (DB + auth + hosting + access control), ~1-2 months**

**Recommendation:** Start with **Option A**. Once it's live and your
teammates have used it for a season, you'll have real feedback on whether
Option B is worth the complexity. Migrating from A → B later is possible
(the read-only views don't change; you're just adding write access).

---

## Phases (assuming Option A: read-only shared views)

### Phase 1: Data layer — file-based → database

**Current state:**
```
app/roster_store.py        → reads/writes to data/raw/rosters/yahoo_roster.json
app/strategy.py            → reads rankings from data/raw/rankings/yahoo_rankings.json
app/web.py routes          → direct JSON.load() calls to data/raw/ and data/processed/
```

**After Phase 1:**
```
app/models/db.py           → SQLAlchemy ORM models for rosters, rankings, draft_history, etc.
app/db/queries.py          → query functions replacing direct file reads
app/cli.py                 → same CLI commands, but "save" steps write to DB instead of JSON
```

**What to do:**

1. **Pick a database.** For a hobby league site:
   - **SQLite** — if you host on a single process (e.g., Render, Railway).
     Zero setup, file-based. Enough for 12 people.
   - **Postgres** — if you want something more robust or are considering
     multi-process hosting later. Supabase or Neon offer free tiers and zero
     ops.

2. **Define schema.** Mirror your existing JSON shapes:
   - `rosters` table: team, player_name, position, nfl_team, rank, etc.
   - `rankings` table: player_name, ranking, position, source, etc.
   - `draft_history` table: year, round, pick, player_name, team
   - `draft_picks` table: year, team, round, pick_count, origin_team
   - `standings` table: year, team, wins, losses, points_for, etc.
   - `keeper_board_snapshots` table: timestamp, team, chosen_keepers[], board_rank[]
     (store the computed output so teammates see historical snapshots)

3. **Migrate the CLI.** Update `app/cli.py` commands to write to the DB
   after they compute. For example:
   - `python -m app parse-rosters` — parses Yahoo text, saves to
     `rosters` table (not `data/raw/rosters/yahoo_roster.json`)
   - `python -m app keepers-board` — computes keepers, saves to
     `keeper_board_snapshots` table (not `data/processed/rankings_post_keepers.csv`)

4. **Swap the web routes.** Update `app/web.py` to query the DB instead of
   reading files. Example:
   ```python
   # Before
   def load_dashboard_state():
       roster = load_roster()  # reads JSON
       rankings = load_yahoo_rankings()  # reads JSON
   
   # After
   def load_dashboard_state():
       roster = db.session.query(Roster).filter_by(team='Wuf').all()
       rankings = db.session.query(Ranking).all()
   ```

**Tools:**
- SQLAlchemy for ORM (already common in Flask apps)
- Alembic for migrations (optional, but helpful if you change schema)
- `python-dotenv` for the DB URL env var (already in requirements.txt)

**Effort:** ~1 week

---

### Phase 2: Web app — local Flask → hosted

**Current state:**
```
python -m app.web
→ runs on http://localhost:8000
→ reads/writes files on your machine
```

**After Phase 2:**
```
wuff.example.com
→ runs on Render/Railway/Fly.io
→ reads/writes to DB (from Phase 1)
→ teammates can visit in a browser
```

**What to do:**

1. **Pick a host.** For a low-traffic Flask app:
   - **Render** or **Railway** — simplest for Flask. Deploy via git push,
     automatic SSL, free tier (0.5–1 CPU, shared RAM, sleeps after 15 min
     inactivity — fine for a hobby app).
   - **Fly.io** — slightly more ops, but fast and good for scaling if you
     ever need it.
   - Avoid: raw AWS/GCP — overkill for this use case.

2. **Prepare for hosting.**
   - Ensure `app/web.py` can read env vars (it already does with
     `python-dotenv`):
     ```python
     DATABASE_URL = os.getenv('DATABASE_URL')
     SECRET_KEY = os.getenv('SECRET_KEY')  # for Flask session signing
     ```
   - Ensure all files are committed (no local paths like
     `/Users/mattwufsus/...` in the code)
   - Create a `Procfile` (for Render/Heroku-like hosts):
     ```
     web: python -m app.web
     ```

3. **Set up secrets.** In your host's dashboard:
   - `DATABASE_URL` — connection string for your Postgres (from Supabase/Neon)
   - `SECRET_KEY` — a random string for Flask session signing
   - `YAHOO_CLIENT_ID`, `YAHOO_CLIENT_SECRET` — your OAuth app credentials
     (these were in `.env` locally; now they're in the host's secret manager)

4. **Run migrations.** If using Alembic:
   ```bash
   # Before first deploy, create the schema in your Postgres database
   alembic upgrade head
   ```

5. **Deploy.** Push to your host (Render: push to GitHub, auto-deploys;
   Railway: same; Fly: `fly deploy`).

**Effort:** ~3–4 days, mostly config + learning your host's UI

---

### Phase 3: Access control — wide open → private

**Current state:**
wuff.example.com is publicly accessible by anyone who finds the URL.

**After Phase 3:**
Only you and your league mates can access it.

**Options (pick one):**

1. **Shared password** (simplest)
   - Add a single login form: username = anything, password = shared secret
   - Store the password hash in the DB or env var
   - Teammates share the password in your league chat
   - Pro: dead simple, works for 12 people
   - Con: not a "real" auth system, password sharing is awkward

2. **Unguessable URL** (middle ground)
   - No login; just a secret slug in the URL: `wuff.example.com/league/xk8J2pQ9...`
   - Share the full URL in league chat; if someone leaks it, change the slug
   - Pro: easy, no login UX, hard for strangers to guess
   - Con: if URL leaks, anyone with it can access

3. **Per-user OAuth** (overkill for Option A, but shows the path to Option B)
   - Add a `users` table: `id`, `email`, `yahoo_token`, etc.
   - Teammates log in with their Yahoo account or an invite link
   - Pro: proper auth, audit trail
   - Con: way more complex; defer until you want Option B

**Recommendation:** Start with option 2 (unguessable URL). If privacy becomes
an issue, swap to a shared password (few lines of Flask code).

**Effort:** ~1 day for shared password; ~3 days for proper OAuth

---

### Phase 4: Refresh cadence — manual vs. scheduled

**Current state:**
You run `python -m app parse-rosters`, `python -m app keepers-board`, etc.
whenever you want to update.

**After Phase 4:**
Pick how the hosted site's data stays fresh.

**Options:**

1. **Manual refresh (via a web button)**
   - Add a "Refresh now" button to `/` that re-runs the CLI commands
   - Only you can click it (gated by Phase 3 auth)
   - Pro: simple, you control when updates happen, good for draft season
   - Con: teammates might see stale data if you forget to refresh

2. **Scheduled refresh (cron job)**
   - Set up a GitHub Action or a cron job on the host that runs the CLI
     commands daily (or weekly)
   - Pros: automated, no manual work
   - Con: might update at the wrong time (during draft night)

3. **Hybrid** (recommended)
   - Scheduled refresh daily (e.g., 7am)
   - Also expose a manual "refresh" button for you during draft season

**Effort:** ~1–2 hours (a few lines of code or host config)

---

## Timeline estimate (Option A, start to finish)

| Phase | Work | Effort | When |
|-------|------|--------|------|
| 1 | DB schema + migrate CLI | 1 week | First |
| 2 | Deploy to Render/Railway | 3–4 days | After Phase 1 |
| 3 | Add password/URL access control | 1–3 days | Before inviting teammates |
| 4 | Set up refresh cadence | 1–2 hours | Anytime after Phase 2 |
| **Total** | | **2–3 weeks** | **Next offseason?** |

---

## If you pick Option B later: what changes?

If after a season teammates ask "can I refresh my own keeper insight?", the
path to Option B is:

1. Add a `users` table (id, email, yahoo_token, etc.)
2. Modify the web routes to be per-user:
   ```python
   @app.route('/dashboard')
   @login_required
   def dashboard():
       roster = load_roster(current_user.id)  # per-user, not global
       ...
   ```
3. Extend `app/oauth_server.py` to store per-user tokens in the DB
4. Update the CLI to accept a `--user` flag (or run it per-user via a
   scheduled job that loops over users)

This is doable without a full rewrite of Phase 1–2; mostly just adding user
context to the DB queries. Plan for ~1–2 additional weeks if you go this
route.

---

## Things NOT in scope

- No billing or monetization
- No multi-league support (just Frank Gore Memorial League)
- No horizontal scaling (this hobby league site will handle 12 people)
- No CI/CD beyond host's built-in deploys
- No monitoring/alerting (Sentry/DataDog overkill; just check the site works)
