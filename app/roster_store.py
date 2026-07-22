import json
from pathlib import Path
from typing import Any, Dict, List

from .paths import YAHOO_ROSTER_FILE, ensure_parent_dir
from .yahoo_client import YahooRosterPlayer

ROSTER_FILE = YAHOO_ROSTER_FILE


def save_roster(roster: List[Dict[str, Any]], path: Path = ROSTER_FILE) -> None:
    ensure_parent_dir(path)
    path.write_text(json.dumps(roster, indent=2))


def load_roster(path: Path = ROSTER_FILE) -> List[YahooRosterPlayer]:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    parsed: List[YahooRosterPlayer] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        player_id = item.get('playerId')
        player_name = item.get('playerName')
        if not player_id or not player_name:
            continue
        parsed.append(
            YahooRosterPlayer(
                playerId=player_id,
                playerName=player_name,
                position=item.get('position', 'UNK'),
                team=item.get('team', 'UNK'),
                status=item.get('status'),
                selectedPosition=item.get('selectedPosition'),
                eligibleSlots=item.get('eligibleSlots'),
                draftRound=item.get('draftRound'),
                draftPick=item.get('draftPick'),
                draftSlot=item.get('draftSlot'),
                keeperEligibleOverride=item.get('keeperEligibleOverride'),
                keeperLockedOverride=item.get('keeperLockedOverride'),
                marketRoundOverride=item.get('marketRoundOverride'),
                valueNote=item.get('valueNote'),
                keeperNote=item.get('keeperNote'),
            )
        )
    return parsed
