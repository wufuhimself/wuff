"""Standings loader and snake draft order derivation.

Reads data/raw/standings/{year}.json.
Next season's draft order = inverse of final standings (worst picks first), snake each round.
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .paths import RAW_STANDINGS_DIR

_RENAME_NOTE_RE = re.compile(r"now displayed as '([^']+)'")


def load_standings(year: int, directory: Path = RAW_STANDINGS_DIR) -> Optional[List[Dict[str, Any]]]:
    """Return the standings list for a season, sorted by rank ascending (1 = best)."""
    path = directory / f'{year}.json'
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    standings = payload.get('standings', [])
    return sorted(standings, key=lambda row: row.get('rank', 9999))


def draft_order_from_standings(standings: List[Dict[str, Any]]) -> List[str]:
    """Reverse-standings draft order for round 1 of a snake draft: worst record picks first."""
    return [row['team'] for row in sorted(standings, key=lambda row: -row.get('rank', 0))]


def current_team_names(standings: List[Dict[str, Any]]) -> Dict[str, str]:
    """Map each standings row's team name to its current display name, using the 'note' field
    ("This is the team now displayed as 'X'.") saved when a manager renames their team --
    display names change year to year even though the 12 manager slots don't."""
    aliases: Dict[str, str] = {}
    for row in standings:
        note = row.get('note', '')
        match = _RENAME_NOTE_RE.search(note)
        if match:
            aliases[row['team']] = match.group(1)
    return aliases


def snake_draft_order(round1_order: List[str], rounds: int) -> Dict[int, List[str]]:
    """Full snake draft order: round 1 as given, round 2 reversed, round 3 as given, etc."""
    order: Dict[int, List[str]] = {}
    for round_number in range(1, rounds + 1):
        if round_number % 2 == 1:
            order[round_number] = list(round1_order)
        else:
            order[round_number] = list(reversed(round1_order))
    return order
