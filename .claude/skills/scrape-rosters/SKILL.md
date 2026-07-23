---
name: scrape-rosters
description: >
  Web scrape Yahoo Fantasy Football rosters using Selenium. Fetch your own roster, scrape entire
  league rosters, or export league snapshots to CSV. Triggers on "scrape roster", "fetch roster",
  "save roster", "league rosters", "export rosters", "/scrape-rosters", or when user needs roster data.
---

Automate roster scraping and export without asking.

## Commands by workflow

### Your own roster

```bash
python3 -m app save-roster
python3 -m app saved-roster
```

**save-roster:** Fetches your current Yahoo roster and stores locally to `data/raw/rosters/yahoo_roster.json`.

**saved-roster:** Prints the locally saved roster (no API call, offline).

### Entire league rosters

```bash
python3 -m app scrape-league-rosters
python3 -m app scrape-league-rosters --teams 12 --headless
```

Scrapes every team's roster in the league and saves a full snapshot to:
`data/raw/rosters/yahoo_league_rosters.json`

**Flags:**
- `--teams N`: number of teams in league (default 12)
- `--headless`: run browser in headless mode (default: visible browser)
- `--output PATH`: custom output path

### Export league rosters to CSV

```bash
python3 -m app export-league-rosters-csv
python3 -m app export-league-rosters-csv --input data/raw/rosters/yahoo_league_rosters.json --output data/processed/rosters/yahoo_league_rosters.csv
```

Converts saved JSON snapshot to clean CSV format for analysis.

### Fetch via API (no scraping)

For API-based roster fetch (if you have OAuth access token):

```bash
python3 -m app yahoo-roster <accessToken>
python3 -m app yahoo-keepers <accessToken>
python3 -m app roster-raw <accessToken>
```

## Prerequisites

Scraping requires:
- Firefox browser (installed via Homebrew: `brew install firefox`)
- Geckodriver (installed via Homebrew: `brew install geckodriver`)
- Selenium + beautifulsoup4 (in `requirements.txt`)

## Workflow

1. **Save your roster**: `save-roster` — use for keeper insight and personal analysis
2. **Scrape league rosters**: `scrape-league-rosters` — needed for keeper-board and forecast analysis
3. **Export to CSV**: `export-league-rosters-csv` — for offline review or sharing

Output files:
- `data/raw/rosters/yahoo_roster.json` — your roster (JSON)
- `data/raw/rosters/yahoo_league_rosters.json` — all teams (JSON snapshot)
- `data/processed/rosters/yahoo_league_rosters.csv` — all teams (CSV)

## Headless mode

Default shows Firefox browser during scraping. Use `--headless` for background execution:

```bash
python3 -m app scrape-league-rosters --headless
```

## Auto-triggers

Run roster scraping when user:
- Asks for keeper/draft board analysis
- Requests league-wide player snapshots
- Mentions rosters are outdated
- Prepares draft strategy
- Requests "scrape rosters" or "league rosters"
