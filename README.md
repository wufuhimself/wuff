# wuff — Frank Gore Memorial League GM tool

Keeper recommendations, draft board rankings, and strategic analysis for Yahoo Fantasy Football.

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

# Analyze rank vs ADP to find value plays
python3 -m app adp-value-analysis --export data/processed/analysis.csv
```

### **Draft history & league trends**
```bash
# Analyze historical draft data
python3 -m app draft-history 2025 --live-only
python3 -m app draft-order 2025
```

## Web dashboard

Run `make web` or `make web-debug` for hot-reload during development.

**Routes:**
- `/` — Dashboard with keeper impact + ADP analysis
- `/keepers-board` — Team keeper picks + draft board
- `/draft-history` — Historical draft results by year
- `/standings` — League standings and performance

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

**Keeper logic:** `app/strategy.py` — eligibility, scoring, and selection

**Full command reference:** `python3 -m app --help`

## Keeper rules

- **Cost:** 2 keepers per team, no round cost (occupy last 2 rounds)
- **Eligibility:** Round 1 or 2 pick from last draft ineligible
- **Duration:** Max 2 consecutive seasons as keeper
- **Scoring:** Rank-first, with positional scarcity + multi-year tenure as tiebreaks

Details: `data/config/league_rules.json` + `CLAUDE.md`
