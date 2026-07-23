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

**Import rankings from CSV** (e.g., FantasyPros, ESPN):

```bash
python3 -m app import-rankings-csv ./rankings.csv
python3 -m app import-rankings-csv ./rankings.csv --source FantasyPros
```

**Import rankings from PDF**:

```bash
python3 -m app import-rankings-pdf ./rankings.pdf
python3 -m app import-rankings-pdf ./rankings.pdf --source "FantasyPros"
```

**Combine all ranking sources** into a consensus file:

```bash
python3 -m app combine-rankings
```

This reads all CSV/JSON/PDF files from `data/raw/rankings/` (except `rankings_combined.json`),
normalizes player IDs, and averages ranks across sources into `data/raw/rankings/rankings_combined.json`.

## Workflow

1. **Refresh Yahoo** to get the latest projected rankings
2. **Import manual CSVs** from other sites (FantasyPros, ESPN, etc.) as needed
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
