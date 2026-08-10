---
name: refresh-rankings
description: >
  Refresh player rankings from Yahoo Fantasy or import from CSV/PDF. Handles fetching
  projected rankings, importing manually downloaded files, and combining multiple sources
  into a consensus ranking. Triggers on "refresh rankings", "import rankings", "update rankings",
  "combine rankings", or "/refresh-rankings".
---

Automate ranking updates without asking.

## Common commands

**Refresh Yahoo rankings** (requires Yahoo OAuth access token):

```bash
python3 -m app refresh-yahoo-rankings
python3 -m app refresh-yahoo-rankings --count 300
```

**Refresh from free sources (the standard path)**:

```bash
python3 -m app refresh-free-rankings
```

**Import rankings from a user-supplied CSV**:

```bash
python3 -m app import-rankings-csv ./rankings.csv
python3 -m app import-rankings-csv ./rankings.csv --source MyRankings
```

**Import rankings from PDF**:

```bash
python3 -m app import-rankings-pdf ./rankings.pdf
python3 -m app import-rankings-pdf ./rankings.pdf --source "MyRankings"
```

**Combine all ranking sources** into a consensus file:

```bash
python3 -m app combine-rankings
```

This reads all CSV/JSON/PDF files from `data/raw/rankings/` (except `rankings_combined.json`),
normalizes player IDs, and averages ranks across sources into `data/raw/rankings/rankings_combined.json`.

## Standard workflow (2026-08-10+): free sources, refreshed daily

FantasyPros data was removed 2026-08-10 (licensing — can't redistribute on a
public site). The standard board now comes from **free sources**: FFC's free
PPR ADP API (real mock-draft market data) plus a Sleeper search-rank tail
for depth, with the historical QB adjustment applied automatically.

```bash
# One command does it all: fetch FFC ADP + Sleeper tail, write
# yahoo_rankings.json (QB-adjusted working board), rankings_combined.json
# (pure market board), and adp_combined.json.
python3 -m app refresh-free-rankings
python3 -m app refresh-free-rankings --scoring half-ppr --teams 10

# Regenerate the keeper board + draft board from these rankings
python3 -m app keepers-board
python3 -m app keepers-board-export
```

The web app runs `refresh-free-rankings` automatically once a day via the
background scheduler — the manual command is for immediate refreshes.

`apply-qb-adjustment` still exists for re-tuning the QB shift on an already
saved board (`--top-n`, `--years` flags) but refresh-free-rankings already
applies it.

Do **not** run generic `combine-rankings` after this unless you intend to go back to a
multi-source blend — it re-averages every CSV/JSON/PDF in `data/raw/rankings/`
(including old superflex/ESPN files that may still be sitting there) and will
overwrite the single-source PPR+QB-adjusted `rankings_combined.json` that
`apply-qb-adjustment` just wrote.

## Legacy workflow (multi-source consensus, superflex-era)

1. **Refresh Yahoo** to get the latest projected rankings
2. **Import manual CSVs** from other sites as needed
3. **Combine all** to get consensus ranking across sources

Output files go to `data/raw/rankings/`. Keeper and draft board commands automatically use
the highest-priority available ranking (Yahoo > combined > individual sources).

## Auto-triggers

Run ranking refreshes proactively when user:
- Asks to "refresh/update rankings"
- Prepares for keeper/draft decisions
- Mentions downloading rankings from a new source
- Indicates keeper deadline or draft is approaching

## Notes

- Save CSV files to `data/raw/rankings/{source}_rankings.csv` before importing
- PDF importer expects tables or text in format: `N. (POS#) PlayerName, TEAM`
- CSV must have columns like: `playerName`, `player`, `rank`, `ranking`, `position`, `team`
- All sources get normalized to a standard player ID scheme
