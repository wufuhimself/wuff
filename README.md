# wuff — Fantasy Football GM tool

Keeper recommendations, draft board rankings, and strategic analysis for fantasy football —
not tied to one platform.

## What this is

Most fantasy tools are dashboards: they show data, you decide. wuff is designed as an
**agent** — something that perceives league state, reasons about decisions using
league-specific learned patterns (not just generic expert consensus), and is working
toward eventually acting on those decisions itself. The core bet: a league's own multi-year
history is more predictive of *this* league's behavior than a national ranking site — see
`app/qb_historical_adjustment.py` for a concrete example (QB draft-slot targets computed
fresh from this league's own draft history each run, not hand-tuned).

**Where it stands today:** a strong "on-demand advisor" for the Yahoo league — ask it for a
keeper board, a mock draft, or a QB-adjusted ranking, and it returns a scored recommendation
with rationale, grounded in that league's own draft/keeper history. Read-only against Yahoo
(write access requires manual API approval, currently pending). The Sleeper side (6 leagues,
fully readonly/public API, no auth needed) is newer and currently visibility-only — rosters,
standings, draft results, no keeper/strategy logic ported over yet.

**What's next:** closing the feedback loop. `app/outcome_log.py` (added 2026-07-31) logs
every keeper and QB-draft-slot forecast the app makes, tagged with which scoring method
produced it, then `resolve-outcomes` matches those forecasts against actual draft results
once a season's draft happens. Right now it *records* forecast accuracy; the next step is
having something read that log back and actually adjust scoring — that's what turns this
from a tool that makes recommendations into one that improves them over time. After that:
a lineup optimizer and trade evaluator (the two most common in-season decisions, neither
built yet), and a platform abstraction layer so the reasoning core stops being Yahoo-shaped.

## Quick start

```bash
# Install dependencies (creates .venv, installs Python packages)
make install

# Set up credentials (copy template and add OAuth tokens)
cp .env.example .env

# Run web dashboard
make web
# Opens at http://127.0.0.1:5001
```

## Common workflows

### **Keeper board & analysis**
```bash
# Parse league rosters from Yahoo (copy-paste text when prompted)
python3 -m app parse-rosters

# Generate keeper recommendations for all teams
python3 -m app keepers-board-export

# View keeper board in web dashboard
make web
```

### **Rankings & draft prep**
```bash
# Combine multiple ranking sources
python3 -m app combine-rankings

# Pull fresh rankings from Yahoo
python3 -m app refresh-yahoo-rankings

# Import ADP (used to enrich keeper forecasts / mock draft)
python3 -m app import-adp data/raw/adp/fantasypros_adp.csv
```

### **Draft history & league trends**
```bash
# Analyze historical draft data
python3 -m app draft-history 2025 --live-only
python3 -m app draft-order 2025
```

### **Sleeper leagues (readonly, no auth needed)**
```bash
# Discover leagues for a Sleeper username (writes data/config/sleeper_leagues.json)
python3 -m app sleeper-discover <username>

# Sync rosters/standings/draft results for all configured leagues
python3 -m app sleeper-sync

# Refresh the shared player_id -> name/position/team cache (occasional, ~5MB fetch)
python3 -m app sleeper-refresh-players

# View in web dashboard: /sleeper
```

## Web dashboard

Run `make web` or `make web-debug` for hot-reload during development.

**Routes:**
- `/` — Dashboard with roster, rankings, and keeper insight
- `/keepers-board` — Team keeper picks + draft board (versioned by snapshot)
- `/mock-draft` — Full 15-round mock draft simulator (BPA + manager tendencies)
- `/draft-history` — Historical draft results by year
- `/standings` — League standings and performance
- `/sleeper` — Sleeper league list (6 leagues); `/sleeper/<league_id>` for standings/rosters/draft

## Setup details

**Requirements:** Python 3.9+

**Installation:**
```bash
make install          # Create venv + install dependencies
make install-dev      # Create venv + install dependencies + pylint
make clean           # Remove venv
```

**Linting:**
```bash
make lint             # Run pylint over app/ (config: .pylintrc)
```

**Yahoo OAuth:**
Register callback at `https://localhost:3000/oauth/callback` in Yahoo app settings, then:
```bash
make auth-server     # Start local HTTPS server for OAuth flow
```

## Architecture

**Configuration:** `data/config/league_rules.json` — keeper rules, file mappings, code ownership

**Key data:**
- `data/raw/rosters/` — League roster snapshots
- `data/raw/rankings/` — Multi-source rankings (Yahoo, ESPN, FantasyPros, etc.)
- `data/raw/draft_history/` — Historical picks by season
- `data/processed/keeper_exports/` — Keeper board CSV exports (timestamped by method)
- `data/processed/outcome_log.json` — Forecast-vs-actual log (generated locally, gitignored); see `app/outcome_log.py`
- `data/raw/sleeper/` — Synced Sleeper league snapshots (rosters, standings, drafts); `data/config/sleeper_leagues.json` for the league list

**Keeper logic:** `app/strategy.py` — eligibility, scoring, and selection

**Full command reference:** `python3 -m app --help`

## Keeper rules

- **Cost:** 2 keepers per team, no round cost (occupy last 2 rounds)
- **Eligibility:** Round 1 or 2 pick from last draft ineligible
- **Duration:** Max 2 consecutive seasons as keeper
- **Scoring:** Rank-first, with positional scarcity + multi-year tenure as tiebreaks

Details: `data/config/league_rules.json` + `CLAUDE.md`
