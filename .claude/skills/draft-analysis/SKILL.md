---
name: draft-analysis
description: >
  Analyze draft history, compute draft order, and analyze draft outcomes. Reads historical picks,
  final standings, derives snake draft order for next season, and correlates draft slot with
  final performance. Triggers on "draft history", "draft order", "draft analysis", "draft slot",
  "/draft-analysis", or when user reviews draft strategy.
---

Automate draft analysis without asking.

## Commands by workflow

### View draft history

```bash
python3 -m app draft-history 2025
python3 -m app draft-history 2025 --round 1
python3 -m app draft-history 2025 --live-only
python3 -m app draft-history 2025 --keepers-only
```

**Flags:**
- `--round N`: show only that round (default: all)
- `--live-only`: exclude keeper-slot rounds (last 2 rounds)
- `--keepers-only`: show only keeper-slot picks

**Why --live-only matters:** Keeper slots reflect retention decisions, not draft-day demand.
Mixing them understates how early players actually go in live draft.

### View draft order

```bash
python3 -m app draft-order 2025
python3 -m app draft-order 2025 --rounds 3
```

Derives next season's snake draft order from final standings. First team's worst record picks #1.

**Flags:**
- `--rounds N`: show only that many rounds

### View pick ownership

```bash
python3 -m app draft-picks 2026
python3 -m app draft-picks 2026 --team Wuf
```

Who owns which pick in each round (accounts for trades).

Reads: `data/raw/draft_picks/{year}.json`

### Analyze draft slot outcomes

```bash
python3 -m app draft-slot-outcomes
python3 -m app draft-slot-outcomes --export-csv data/draft_slot_analysis.csv
```

Correlates draft slot (1–12) with final league finish. Shows:
- Average final rank by slot
- Median finish
- Sample sizes
- Overall correlation

**Flags:**
- `--export-csv PATH`: save results to CSV

### View standings

```bash
python3 -m app standings 2025
```

Final standings for a season (wins, losses, ties, points). Used to derive next season's draft order.

Reads: `data/raw/standings/{year}.json`

## Data format

To add a new season's draft history after a live draft:

```json
{
  "year": 2026,
  "picks": [
    {"round": 1, "pick": 1, "playerName": "Josh Allen", "team": "Wuf"},
    {"round": 1, "pick": 2, "playerName": "Patrick Mahomes", "team": "other team"},
    ...
  ]
}
```

Save to: `data/raw/draft_history/2026.json`

## Key concepts

**Live-only picks:** Picks outside the last 2 keeper-slot rounds. Best for analyzing "when do players
actually go in a competitive draft" because keeper slots are historical retention decisions, not
fresh draft-day demand.

**Snake draft order:** Alternating direction each round. Round 1 sorted by standings (worst first),
round 2 reversed, round 3 sorted again, etc.

**Draft slot correlation:** How much does picking early (slot 1–3) vs. late (slot 10–12) predict final
finish? Accounts for skill, luck, injuries, trades.

## Auto-triggers

Run draft analysis when user:
- Asks about draft strategy or "what pick should I take"
- Reviews historical pick patterns
- Prepares for upcoming draft
- Derives next season's draft order
- Analyzes "which draft slots perform best"
- Requests "draft history" or "draft analysis"
