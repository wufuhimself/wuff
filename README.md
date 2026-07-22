# Yahoo Fantasy Football Analyst

A Python-based Yahoo Fantasy Football analyst for the Frank Gore Memorial League. It
helps Wuf analyze and optimize this stuff:

- aggregates player rankings from multiple sources,
- fetches and stores league rosters, standings, and draft history from Yahoo,
- computes keeper eligibility (round restrictions, consecutive-season caps) and picks the
  best keepers per team,
- derives snake draft order and pick ownership,
- builds a post-keepers draft board CSV,
- interacts with Yahoo Fantasy Football to set lineups.

Everything below is runnable directly from the CLI — no need to script anything ad hoc.

## Getting started

### Prerequisites

This project requires **Python 3.13+** and **Selenium** (for web scraping). Set up via Homebrew:

```bash
# Install Python 3.13 via Homebrew
brew install python@3.13

# Install Gecko driver (Firefox webdriver for Selenium)
brew install geckodriver

# Install Firefox browser (if not already installed)
brew install firefox
```

### Setup

1. Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate  # or use .venv/bin/python directly
```

2. Install Python dependencies:

```bash
pip install -r requirements.txt
```

3. Configure Yahoo OAuth credentials:

```bash
cp .env.example .env
```

Then edit `.env` and fill in your Yahoo OAuth credentials (see [Yahoo OAuth local callback](#yahoo-oauth-local-callback) below for how to obtain them).

**IMPORTANT:** Never commit `.env` to version control — it's in `.gitignore` for this reason.

4. Run the CLI:

```bash
python3 -m app.cli --help
```

For the web dashboard:

```bash
make web
```

## Project structure

- `app/__main__.py` — Python package entrypoint
- `app/cli.py` — command-line interface (every command in this README)
- `app/rankings_aggregator.py` — ranking aggregation logic
- `app/strategy.py` — keeper eligibility + selection logic (see [Keepers](#keepers))
- `app/draft_history.py` — loads `data/raw/draft_history/*.json`, computes the
  consecutive-keeper-season cap, and filters live-draft picks from keeper-slot picks
- `app/draft_picks.py` — loads `data/raw/draft_picks/*.json` (who owns which round picks)
- `app/standings.py` — loads `data/raw/standings/*.json`, derives snake draft order
- `app/league_context.py` — `LeagueFormat` (teams, starters, keeper rules)
- `app/roster_store.py` — load/save your own roster JSON
- `app/yahoo_client.py` — Yahoo API integration
- `app/oauth_server.py` — local OAuth callback server
- `app/config.py` — environment and settings
- `data/config/league_rules.json` — machine-readable league rules + a map of every data file
  and which code module owns it — **read this first** before touching keeper logic
- `data/` — saved artifacts with `raw/` and `processed/` subdirectories

## League rules

The full rules live in `data/config/league_rules.json`. Summary:

- **12 teams**, superflex (1 extra QB-eligible flex slot), 3 WR starters
- **2 keeper slots per team**, no draft-round cost — keepers just occupy the last 2 rounds
  of the draft (round 14/15 in a 15-round draft; was 15/16 back when the league rostered a
  kicker, through 2023)
- A player **can't be kept if drafted in round 1 or 2** of the most recent draft
- A player **can't be kept more than 2 consecutive seasons** — `draft_history.py` walks
  backward through the saved draft-history files to enforce this automatically
- Since there's no draft-round cost, keeper picks should be chosen on **current value**
  (ranking + positional scarcity for this league's actual roster construction), not on
  "rounds saved" — see `select_best_keepers()` in `strategy.py`

## Keepers

Save your league's current rosters, then compute the best 2 keepers for every team and
write a post-keepers draft board in one command:

```bash
python3 -m app.cli keepers-board
```

This reads `data/raw/rosters/yahoo_league_rosters.json` and
`data/raw/rankings/yahoo_rankings.json`, prints the top-2 keeper picks for all 12 teams, and
writes `data/processed/rankings_post_keepers.csv` — the full ranked board with every chosen
keeper removed and a sequential `draftOrder` column (no gaps, unlike the raw `ranking`
column, which skips numbers once players are pulled out).

```
your waterbroke girl: Tyler Warren (74), Garrett Wilson (45)
Jordan Poops Blood: DeVonta Smith (41), Tucker Kraft (87)
...
Wrote 515 players to data/processed/rankings_post_keepers.csv (24 keepers removed)
```

Flags: `--teams` (default 12), `--count` (keeper slots per team, default 2), `--input`/`--output`
to point at different snapshot/CSV paths.

For just your own roster:

```bash
python3 -m app.cli keeper-insight      # full per-player breakdown, why each is/isn't eligible
python3 -m app.cli best-keepers        # just the top N picks + alternates
```

Both read `data/raw/rosters/yahoo_roster.json` (or hit the Yahoo API if nothing's saved) and
`data/raw/rankings/yahoo_rankings.json`. Each player's output includes `keeperYearsRemaining`
— how many more consecutive seasons that player could still be kept before hitting the
2-season cap. It doesn't affect eligibility on its own, but it's a useful tiebreaker: between
two similarly-valued keepers, the one with more years left preserves that value longer.

## Draft history

One JSON file per season in `data/raw/draft_history/{year}.json` — currently 2020-2025.
Used automatically by the keeper-cap logic, but also queryable directly:

```bash
python3 -m app.cli draft-history 2025                  # every pick that year
python3 -m app.cli draft-history 2025 --round 1        # just round 1
python3 -m app.cli draft-history 2025 --live-only      # exclude keeper-slot rounds
python3 -m app.cli draft-history 2025 --keepers-only   # only the keeper-slot picks
```

`--live-only` matters for any "when does position X usually get drafted" analysis: a pick
landing in the last-2-rounds keeper slot reflects a prior-season retention decision, not
fresh draft-day demand, and mixing the two understates how early good players actually go.

To add a new season after a live draft, drop a file in the same shape:

```json
{"year": 2026, "picks": [{"round": 1, "pick": 1, "playerName": "...", "team": "..."}, ...]}
```

## Draft picks and draft order

Who owns which pick in each round (accounts for trades):

```bash
python3 -m app.cli draft-picks 2026
python3 -m app.cli draft-picks 2026 --team Wuf
```

Reads `data/raw/draft_picks/{year}.json`.

Final standings for a season:

```bash
python3 -m app.cli standings 2025
```

Reads `data/raw/standings/{year}.json`. The next season's snake draft order is the inverse
of these standings (worst record picks first), alternating each round:

```bash
python3 -m app.cli draft-order 2025             # derives the 2026 draft order
python3 -m app.cli draft-order 2025 --rounds 3
```

## Rankings

Fetch and save rankings so keeper/draft commands have something to work from:

```bash
python3 -m app.cli refresh-yahoo-rankings
```

Or import a CSV downloaded manually from a rankings site (e.g. FantasyPros):

```bash
python3 -m app.cli import-rankings-csv ./rankings.csv --source FantasyPros
```

The importer looks for common column names such as `playerName`/`player`, `ranking`/`rank`,
plus optional `position`, `team`, `playerId`, and `source` columns. If `playerId` is missing,
the app falls back to player-name matching. Saved rankings live in
`data/raw/rankings/yahoo_rankings.json`.

You can also fetch projected rankings directly from Yahoo and exclude keepers inline:

```bash
python3 -m app.cli yahoo-rankings <accessToken> --keeper "Christian McCaffrey" --keeper 12345
```

## League format

Keeper insight uses your league's actual starter counts (superflex, WR count, etc.) to judge
positional scarcity, not generic rankings. Set it once:

```bash
python3 -m app.cli set-league-format --teams 12 --qb 1 --rb 2 --wr 3 --te 1 --superflex 1 --defense 1 --kicker 0
python3 -m app.cli show-league-format
```

This persists `data/config/league_settings.json`.

## Yahoo OAuth local callback

Yahoo requires an HTTPS redirect URI for OAuth. For local development, register an HTTPS
callback URL such as:

```bash
https://localhost:3000/oauth/callback
```

Then either provide your own certificate paths in `.env`:

```bash
YAHOO_SSL_KEY_PATH=./certs/localhost.key
YAHOO_SSL_CERT_PATH=./certs/localhost.crt
```

Or let the app generate a self-signed cert at runtime for `localhost`.

Start the local OAuth flow with:

```bash
python3 -m app.cli auth-server
```

If your browser warns about the certificate, accept the warning so the redirect can complete.

## Yahoo roster and league data

Fetch a parsed roster list from Yahoo to inspect players and identify keepers:

```bash
python3 -m app.cli yahoo-roster <accessToken>
```

Scrape every team roster in the league and save a full league snapshot (this is the input
`keepers-board` reads):

```bash
python3 -m app.cli scrape-league-rosters --teams 12 --output data/raw/rosters/yahoo_league_rosters.json
```

Convert that saved snapshot into a clean CSV:

```bash
python3 -m app.cli export-league-rosters-csv --input data/raw/rosters/yahoo_league_rosters.json --output data/processed/rosters/yahoo_league_rosters.csv
```

Save your own current Yahoo roster locally for offline keeper analysis:

```bash
python3 -m app.cli save-roster <accessToken>
python3 -m app.cli saved-roster        # print what's saved, no API call
```

Fetch keeper players based on draft-round metadata straight from Yahoo:

```bash
python3 -m app.cli yahoo-keepers <accessToken>
```

Forecast likely opponent keepers before the deadline:

```bash
python3 -m app.cli forecast-keepers
```

Raw Yahoo API response, for debugging:

```bash
python3 -m app.cli roster-raw <accessToken>
```

If you have data in legacy file locations, run a one-time migration:

```bash
python3 -m app.cli migrate-data-layout
```

## Web dashboard

Launch the local dashboard with:

```bash
python3 -m app.web
```

Or via Makefile shortcut:

```bash
make web
```

Then visit `http://127.0.0.1:8000`. Pages:

- **Dashboard** (`/`) — save your roster, refresh/import rankings, your own keeper insight
  (including `keeperYearsRemaining`), likely opponent keepers, top available players
- **Keepers board** (`/keepers-board`) — the same computation as `python3 -m app.cli
  keepers-board`: best 2 keepers for every team in the league, plus the top of the
  post-keepers draft board
- **Draft history** (`/draft-history`) — pick a season, view every pick by round, or filter
  to live-draft-only / keeper-slot-only picks; each year links to its pick-ownership table
- **Standings** (`/standings`) — pick a season's final standings, which links to the derived
  snake draft order for the following season

All of it reads the same JSON files the CLI commands use, so anything you save via `save-roster`,
`refresh-yahoo-rankings`, or by dropping a new file under `data/raw/` shows up here too.

## Data directory map

```
data/
  config/
    league_settings.json   # LeagueFormat: teams, starters, keeper rules
    league_rules.json       # machine-readable rules + file/code map, read this first
  raw/
    rosters/                 yahoo_roster.json, yahoo_league_rosters.json
    rankings/                 yahoo_rankings.json
    draft_history/{year}.json  one file per season, 2020-2025 so far
    draft_picks/{year}.json    pick ownership by round, accounts for trades
    standings/{year}.json      final standings, drives next season's draft order
  processed/
    rosters/yahoo_league_rosters.csv
    rankings_post_keepers.csv   # output of `keepers-board`
```
