---
name: league-setup
description: >
  Configure league format and standings. Set team counts, starter positions, keeper rules,
  and fetch/analyze league standings. Triggers on "league format", "league settings", "league setup",
  "standings", "configure league", "/league-setup", or when user needs league configuration.
---

Automate league configuration without asking.

## Commands by workflow

### Set league format

```bash
python3 -m app set-league-format --teams 12 --qb 1 --rb 2 --wr 3 --te 1 --superflex 1 --defense 1 --kicker 0
```

**Flags (all optional):**
- `--teams N`: number of teams (default 12)
- `--qb N`: starting QB slots per team (default 1)
- `--rb N`: starting RB slots (default 2)
- `--wr N`: starting WR slots (default 2)
- `--te N`: starting TE slots (default 1)
- `--flex N`: starting FLEX slots (default 0)
- `--superflex N`: starting SUPERFLEX slots (default 0)
- `--defense N`: starting DEF slots (default 1)
- `--kicker N`: starting K slots (default 1)

Persists to: `data/config/league_settings.json`

### View league format

```bash
python3 -m app show-league-format
```

Displays currently saved league format.

### Fetch and save standings

```bash
python3 -m app fetch-standings 2025
python3 -m app fetch-standings 2025 --league-id 123456
```

Fetches final standings from Yahoo API and saves to `data/raw/standings/{year}.json`.

Requires: Yahoo OAuth access token

**Flags:**
- `--league-id ID`: override default league ID from .env
- `--year YYYY`: fetch specific season

### Backfill standings (via MCP)

```bash
python3 -m app backfill-standings-mcp --start-year 2022 --end-year 2025
```

Fetch standings for multiple years at once via MCP. Requires MCP server running.

**Flags:**
- `--start-year YYYY`: first season (default 2020)
- `--end-year YYYY`: last season, inclusive (default 2025)

## Why league format matters

Keeper and draft board analysis uses league format to compute **positional scarcity** — how valuable
a player is given your league's specific starter count. Frank Gore Memorial League (FGML) has:
- Superflex (extra QB-eligible flex), which raises QB value
- 3 WRs, which lowers WR value vs. other positions
- No kicker, which raises bench flex value

This affects keeper selection and draft strategy.

## Data files

- `data/config/league_settings.json` — LeagueFormat (teams, starters)
- `data/config/league_rules.json` — read this first (keeper rules + file/code map)
- `data/raw/standings/{year}.json` — final standings (wins, losses, points)

## Auto-triggers

Configure league settings when user:
- First time using the tool (needs baseline setup)
- League format changes (new superflex, different roster size, etc.)
- Needs standings for draft order or strategy analysis
- Requests "league format", "league settings", or "standings"
