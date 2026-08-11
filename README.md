# 🧙‍♂️ Gridiron Sage

*A grimoire for keeper decisions, draft boards, and roster strategy — cast across leagues, not bound to one platform.*

## 🔮 What this is

Most fantasy tools are dashboards: they show data, you decide. The Gridiron Sage is conjured as an
**agent** — something that perceives league state, reasons about decisions using
league-specific learned patterns (not just generic expert consensus), and is working
toward eventually acting on those decisions itself. The core spell: a league's own
multi-year history is more predictive of *this* league's behavior than a national
ranking site — see `app/qb_historical_adjustment.py` for a concrete example (QB
draft-slot targets computed fresh from this league's own draft history each run, not
hand-tuned).

**📍 Where it stands today:** a strong "on-demand advisor" across 7 leagues — 1 Yahoo
(deepest-supported, keeper/draft rules live) + 6 Sleeper (readonly). Interactive keeper
selection lives at `/keepers-board` (Yahoo) and `/league/<slug>/keepers` (any league) —
click a player card to toggle kept/not-kept, no login, live AJAX updates. Read-only
against Yahoo (write access requires manual API approval, currently pending). ESPN import
is in beta. Multi-user accounts (Phase 1) exist — Sleeper username onboarding included.

**⚡ What's next:** closing the feedback loop. `app/outcome_log.py` logs every keeper and
QB-draft-slot forecast the app makes, tagged with which scoring method produced it, then
`resolve-outcomes` matches those forecasts against actual draft results once a season's
draft happens. Right now it *records* forecast accuracy; the next step is having something
read that log back and actually adjust scoring — that's what turns this from a tool that
makes recommendations into one that improves them over time. After that: a lineup
optimizer and trade evaluator (the two most common in-season decisions, neither built yet).

## 🗡️ The band

The Gridiron Sage is the wizard at the table — but he doesn't work alone. Each piece of the app is a
hero he consults:

| Hero | Domain | Where |
|---|---|---|
| 🔮 **The Oracle** | Keeper forecasting — who to keep, scored and ranked | `/keepers-board`, `/league/<slug>/keepers` |
| 🛡️ **The Paladin** | Standings & rosters — keeps the realm's order straight | `/standings`, `/`, `/sleeper/<id>`, `/espn/<id>` |
| ⚔️ **The General** | Mock draft — recruits allies, simulates the battle ahead | `/mock-draft` |
| 📜 **The Maester** | Draft history, draft order & draft-outcome analysis — keeper of records, past seasons on the shelf | `/draft-history`, `/draft-order`, `/draft-picks`, `/league/<slug>/draft-analysis` |
| 📯 **The Herald** | Multi-league management — announces and onboards new realms | `/leagues`, `/my/leagues`, `/my/onboard`, `/sleeper` |
| 🕰️ **The Augur** | Forecast accuracy tracking (no web page yet) | `app/outcome_log.py`, `resolve-outcomes` |

More heroes join the band as new domains get built out (see
`WS-6-agent-runtime/Band_of_Heroes.md` in the vault for the full roster + unassigned
domains).

## 🚀 Quick start

```bash
# Install dependencies (creates .venv, installs Python packages)
make install

# Set up credentials (copy template and add OAuth tokens)
cp .env.example .env

# Run web dashboard
make web
# Opens at http://127.0.0.1:5001
```

## 🏈 Common workflows

### **🃏 Keeper board & analysis** — 🔮 The Oracle
```bash
# Parse league rosters from Yahoo (copy-paste text when prompted)
python3 -m app parse-rosters

# View + select keepers interactively in the web dashboard
make web
# → /keepers-board (Yahoo) or /league/<slug>/keepers (any league)

# CLI-only: export keeper recommendations to CSV (autonomous agent, not yet wired to web)
python3 -m app keepers-board-export
```

### **📊 Rankings & draft prep**
```bash
# Pull free consensus rankings (FFC ADP + Sleeper search-rank, QB adjustment auto-applied)
python3 -m app refresh-free-rankings

# Combine multiple ranking sources
python3 -m app combine-rankings

# Import ADP (used to enrich keeper forecasts / mock draft)
python3 -m app import-adp path/to/your_adp.csv
```

### **📜 Draft history & league trends** — 📜 The Maester
```bash
# Analyze historical draft data
python3 -m app draft-history 2025 --live-only
python3 -m app draft-order 2025

# Did draft slot predict finish? Which positions in round N did?
# --league works on any registered league (omit for the default one)
python3 -m app draft-slot-outcomes
python3 -m app position-round-outcomes 1 --league frank-gore
```

### **😴 Sleeper leagues (readonly, no auth needed)**
```bash
# Discover leagues for a Sleeper username (writes data/config/sleeper_leagues.json)
python3 -m app sleeper-discover <username>

# Sync rosters/standings/draft results for all configured leagues
python3 -m app sleeper-sync

# Refresh the shared player_id -> name/position/team cache (occasional, ~5MB fetch)
python3 -m app sleeper-refresh-players

# View in web dashboard: /sleeper
```

## 🖥️ Web dashboard

Run `make web` or `make web-debug` for hot-reload during development.

**Routes:**
- `/` — Dashboard with roster, rankings, and keeper insight
- `/keepers-board` — 🔮 The Oracle: interactive keeper card picker for the Yahoo league (click to toggle kept)
- `/league/<slug>/keepers` — 🔮 The Oracle, for any registered league
- `/league/<slug>/draft-analysis` — 📜 The Maester: draft slot vs final rank, and position-by-round outcomes, for any registered league
- `/mock-draft` — ⚔️ The General: full 15-round mock draft simulator (BPA + manager tendencies)
- `/draft-history` — 📜 The Maester: historical draft results by year
- `/standings` — 🛡️ The Paladin: league standings and performance
- `/sleeper` — 📯 The Herald: Sleeper league list (6 leagues); `/sleeper/<league_id>` for standings/rosters/draft (🛡️ The Paladin)
- `/my/leagues`, `/my/onboard` — 📯 The Herald: multi-user login + league import (Sleeper username, ESPN league ID)

## ⚙️ Setup details

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

## 🏛️ Architecture

**Configuration:**
- `data/config/league_rules.json` — Yahoo league keeper rules, file mappings, code ownership
- `data/config/leagues.json` — cross-platform league registry (all 7 leagues, format + rules)

**Key data:**
- `data/raw/rosters/` — League roster snapshots
- `data/raw/rankings/` — Multi-source rankings (free FFC ADP + Sleeper search-rank tail)
- `data/raw/draft_history/` — Historical picks by season
- `data/processed/keeper_exports/` — CLI keeper export CSVs (timestamped by method)
- `data/processed/outcome_log.json` — Forecast-vs-actual log (generated locally, gitignored); see `app/outcome_log.py`
- `data/raw/sleeper/` — Synced Sleeper league snapshots (rosters, standings, drafts)
- `data/raw/espn/` — Synced ESPN league snapshots (beta)
- `data/wuff.db` — Multi-user state: accounts, league links, sync runs, keeper overrides (gitignored)

**Keeper logic:** `app/strategy.py` (Yahoo) + `app/league_service.py` (per-league, any platform)

**Full command reference:** `python3 -m app --help`

## 📖 Keeper rules (Yahoo league)

- **Cost:** 2 keepers per team, no round cost (occupy last 2 rounds)
- **Eligibility:** Round 1 or 2 pick from last draft ineligible
- **Duration:** Max 2 consecutive seasons as keeper
- **Scoring:** Rank-first, with positional scarcity + multi-year tenure as tiebreaks

Other leagues set their own rules in `data/config/leagues.json` / DB overrides — see
`app/league_service.resolve_league()`.

Details: `data/config/league_rules.json` + `CLAUDE.md`

---

*🔮 May your keepers be correctly forecasted and your sleepers found before Week 1.*
