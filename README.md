# wuff — Frank Gore Memorial League GM tool

Keepers. Draft board. Rankings. All in one place.

## Setup

Requires Python 3.13+, Firefox, geckodriver.

```bash
brew install python@3.13 geckodriver firefox
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in Yahoo OAuth creds — never commit this
```

## Key commands

```bash
# Who to keep (all 12 teams + post-keepers draft board)
python3 -m app keepers-board-export

# Your roster's keeper options
python3 -m app keeper-insight
python3 -m app best-keepers

# Update league rosters (paste Yahoo roster text when prompted)
python3 -m app parse-rosters

# Rankings
python3 -m app combine-rankings          # merge all sources in data/raw/rankings/
python3 -m app refresh-yahoo-rankings    # pull live from Yahoo

# Draft history + order
python3 -m app draft-history 2025 --live-only
python3 -m app draft-order 2025

# Web dashboard (http://127.0.0.1:8000)
make web
```

Full command list: `python3 -m app --help`

## Keeper rules

- 2 keepers per team, no round cost (occupy last 2 rounds)
- Round 1 or 2 pick from last draft = ineligible
- 2-consecutive-season cap enforced automatically
- Scoring: rank first, positional scarcity + years remaining as tiebreaks

Full rules: `data/config/league_rules.json`

## OAuth

Yahoo needs HTTPS for local OAuth. Register `https://localhost:3000/oauth/callback`, then:

```bash
python3 -m app auth-server   # follow browser prompt, accept cert warning
```
