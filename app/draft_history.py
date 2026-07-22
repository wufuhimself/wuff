import json
from pathlib import Path
from typing import Dict, List, Optional

from .paths import RAW_DRAFT_HISTORY_DIR


def _normalize_name(value: str) -> str:
    return ' '.join(value.strip().lower().split())


def load_draft_years(directory: Path = RAW_DRAFT_HISTORY_DIR) -> Dict[int, List[dict]]:
    years: Dict[int, List[dict]] = {}
    if not directory.exists():
        return years
    for path in sorted(directory.glob('*.json')):
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        year = payload.get('year')
        picks = payload.get('picks')
        if isinstance(year, int) and isinstance(picks, list):
            years[year] = picks
    return years


def draft_round_for_player(player_name: str, year: int, years: Optional[Dict[int, List[dict]]] = None) -> Optional[int]:
    years = years if years is not None else load_draft_years()
    picks = years.get(year, [])
    normalized = _normalize_name(player_name)
    for pick in picks:
        if _normalize_name(str(pick.get('playerName', ''))) == normalized:
            return pick.get('round')
    return None


def rounds_in_year(year: int, years: Optional[Dict[int, List[dict]]] = None) -> Optional[int]:
    """Total number of draft rounds that year (16 through 2023 when a kicker was rostered, 15 from 2024 on)."""
    years = years if years is not None else load_draft_years()
    picks = years.get(year)
    if not picks:
        return None
    return max(pick.get('round', 0) for pick in picks)


def keeper_rounds_for_year(year: int, years: Optional[Dict[int, List[dict]]] = None) -> Optional[set]:
    """The last-2-rounds keeper-slot rounds for a given draft year (shifts with total round count)."""
    total_rounds = rounds_in_year(year, years)
    if total_rounds is None:
        return None
    return {total_rounds - 1, total_rounds}


def live_draft_picks(year: int, years: Optional[Dict[int, List[dict]]] = None) -> List[dict]:
    """Picks from the actual live draft only — excludes the last-2-rounds keeper slots.

    A pick landing in a keeper-slot round reflects a retention decision from the prior
    season, not fresh draft-day demand. Mixing those into 'when does position X get
    drafted' analysis understates true demand for the players good enough to be kept.
    """
    years = years if years is not None else load_draft_years()
    picks = years.get(year, [])
    keeper_rounds = keeper_rounds_for_year(year, years)
    if keeper_rounds is None:
        return list(picks)
    return [pick for pick in picks if pick.get('round') not in keeper_rounds]


def keeper_slot_picks(year: int, years: Optional[Dict[int, List[dict]]] = None) -> List[dict]:
    """Picks that occupied a keeper slot that year — i.e. players kept from the prior season."""
    years = years if years is not None else load_draft_years()
    picks = years.get(year, [])
    keeper_rounds = keeper_rounds_for_year(year, years)
    if keeper_rounds is None:
        return []
    return [pick for pick in picks if pick.get('round') in keeper_rounds]


def consecutive_keeper_years(
    player_name: str,
    latest_year: int,
    keeper_rounds: Optional[set] = None,
    years: Optional[Dict[int, List[dict]]] = None,
) -> int:
    """Count how many years in a row, ending at latest_year, this player occupied a keeper-slot round.

    keeper_rounds, if given, is used for every year checked. Leave it unset to let each year use its
    own last-2-rounds slot (handles the 16-round-to-15-round shift when the kicker spot was dropped).
    """
    years = years if years is not None else load_draft_years()
    streak = 0
    year = latest_year
    while True:
        round_drafted = draft_round_for_player(player_name, year, years)
        year_keeper_rounds = keeper_rounds if keeper_rounds is not None else keeper_rounds_for_year(year, years)
        if round_drafted is None or year_keeper_rounds is None or round_drafted not in year_keeper_rounds:
            break
        streak += 1
        year -= 1
    return streak
