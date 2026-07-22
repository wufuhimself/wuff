import json
from pathlib import Path
from typing import Dict, Optional

from .paths import RAW_DRAFT_PICKS_DIR


def load_draft_picks(year: int, directory: Path = RAW_DRAFT_PICKS_DIR) -> Optional[Dict[str, Dict[int, int]]]:
    """Return {teamName: {round: pickCount}} for the given draft year, or None if not saved."""
    path = directory / f'{year}.json'
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    teams = payload.get('teams', [])
    result: Dict[str, Dict[int, int]] = {}
    for team in teams:
        name = team.get('teamName')
        picks_by_round = team.get('picksByRound', {})
        if not name:
            continue
        result[name] = {int(round_str): int(count) for round_str, count in picks_by_round.items()}
    return result


def load_draft_pick_origins(year: int, directory: Path = RAW_DRAFT_PICKS_DIR) -> Optional[Dict[str, Dict[int, list]]]:
    """Return {teamName: {round: [originalSlotTeam, ...]}} -- which original snake slot(s) each
    team's picks in a round came from (post-trade). None if the saved file has no origin data."""
    path = directory / f'{year}.json'
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    teams = payload.get('teams', [])
    result: Dict[str, Dict[int, list]] = {}
    for team in teams:
        name = team.get('teamName')
        origins = team.get('picksByRoundOrigins')
        if not name or origins is None:
            continue
        result[name] = {int(round_str): list(owners) for round_str, owners in origins.items()}
    return result or None


def picks_for_team(team_name: str, year: int, directory: Path = RAW_DRAFT_PICKS_DIR) -> Optional[Dict[int, int]]:
    picks = load_draft_picks(year, directory)
    if picks is None:
        return None
    return picks.get(team_name)


def total_picks_in_round(round_number: int, year: int, directory: Path = RAW_DRAFT_PICKS_DIR) -> Optional[int]:
    picks = load_draft_picks(year, directory)
    if picks is None:
        return None
    return sum(team_rounds.get(round_number, 0) for team_rounds in picks.values())
