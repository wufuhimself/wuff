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

## Standard workflow (2026+): PPR base + historical QB adjustment

As of 2026 the standard draft-forecasting rankings are **straight PPR** (not
superflex-inflated), with the top QBs nudged up to match where a QB of that
rank has actually gone in this league's own draft history — not a hand-tuned
"push QBs down" rule. This replaced the old superflex-CSV + `board_adjustments.json`
QB-knockback approach (that older path still exists via `adjust-rankings` /
`rankings_adjusted.json` but is no longer the default for the post-keeper board
or keeper selection).

```bash
# 1. Import a straight PPR rankings CSV (e.g. FantasyPros "ALL" export) as the base
python3 -m app import-rankings-csv ./FantasyPros_2026_Draft_ALL_Rankings.csv --source "FantasyPros 2026 Draft PPR"

# 2. Nudge the top-N QBs (default 7) up to their historical draft-slot target,
#    computed fresh from data/raw/draft_history/*.json each run (excludes keeper-slot
#    rounds, so a QB's keeper round never counts as fresh draft demand). Overwrites
#    yahoo_rankings.json AND mirrors a single-source copy into rankings_combined.json
#    (needed because keepers-board-export reads rankings_combined.json, and generic
#    combine-rankings would otherwise dilute this with any stale superflex/ESPN CSVs
#    still sitting in data/raw/rankings/).
python3 -m app apply-qb-adjustment
python3 -m app apply-qb-adjustment --top-n 5              # fewer QBs adjusted
python3 -m app apply-qb-adjustment --years 2022 2023 2024 2025  # pin specific draft years

# 3. Regenerate the keeper board + draft board from these rankings (see keeper-analysis skill)
python3 -m app keepers-board
python3 -m app keepers-board-export
```

Do **not** run generic `combine-rankings` after this unless you intend to go back to a
multi-source blend — it re-averages every CSV/JSON/PDF in `data/raw/rankings/`
(including old superflex/ESPN files that may still be sitting there) and will
overwrite the single-source PPR+QB-adjusted `rankings_combined.json` that
`apply-qb-adjustment` just wrote.

## Legacy workflow (multi-source consensus, superflex-era)

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
