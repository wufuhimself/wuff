"""Map team identities across seasons via draft slot consistency.

Draft order = inverse of prior season standings (worst record picks 1st, snake each round).
Use this to reliably track teams by slot position across years.
"""
import json
from pathlib import Path
from typing import Dict, List, Set
from collections import defaultdict

from .standings import load_standings, draft_order_from_standings
from .draft_history import load_draft_years
from .paths import PROCESSED_DIR


def get_team_name_at_slot(year: int, slot: int) -> str | None:
    """Get team name from draft history for a given year + draft slot (1-indexed)."""
    draft_years = load_draft_years()
    if year not in draft_years:
        return None

    picks = draft_years[year]
    for pick in picks:
        if pick.get('pick') == slot:
            return pick.get('team')

    return None


def map_teams_by_draft_slot() -> Dict[int, Dict[int, List[tuple[int, str]]]]:
    """Map teams across years using draft slot consistency.

    Returns: {slot: {year: [(standing_position, team_name), ...], ...}, ...}

    This shows which team name was at each draft slot in each year,
    allowing us to track the same owner across years by slot position.
    """
    standings_by_year = load_standings()
    result = defaultdict(lambda: defaultdict(list))

    for year in sorted(standings_by_year.keys()):
        # Draft order for year Y+1 is inverse of standings for year Y
        draft_order_year = year + 1

        standings = standings_by_year.get(year, {}).get('standings', [])
        if not standings:
            continue

        # Compute snake draft order from standings
        ranks = {team['team']: idx for idx, team in enumerate(standings)}

        # Build draft order (worst record picks first, snake each round)
        sorted_teams = sorted(standings, key=lambda x: x.get('rank', 999))
        draft_slots = []

        teams_list = [t['team'] for t in sorted_teams]
        for round_num in range(1, 16):  # 15 rounds
            if round_num % 2 == 1:
                # Odd rounds: worst to best
                draft_slots.extend(teams_list)
            else:
                # Even rounds: best to worst
                draft_slots.extend(reversed(teams_list))

        # Now match to actual draft picks
        for slot_idx, team_name in enumerate(draft_slots, start=1):
            if slot_idx > 180:  # Max slots (15 rounds × 12 teams)
                break

            actual_team = get_team_name_at_slot(draft_order_year, slot_idx)
            if actual_team:
                result[slot_idx][draft_order_year].append((year, team_name, actual_team))

    return result


def build_owner_identity_map() -> Dict[str, Dict[str, any]]:
    """Build owner identity map by tracking slot consistency.

    Returns: {owner_id: {
        'standing_position': standing rank (1-12),
        'names_by_year': {year: team_name, ...},
        'all_names': [all name variants used],
    }, ...}
    """
    from .standings import load_standings as load_standings_year
    draft_years = load_draft_years()

    # Group by standing position across years (same position = same owner slot in snake)
    position_to_owner_id = {}
    owner_map = {}

    # Get all years we have standings data for
    all_years = set(draft_years.keys())
    # Also try to load standings files
    import os
    standings_dir = Path(__file__).parent.parent / 'data' / 'raw' / 'standings'
    if standings_dir.exists():
        for f in standings_dir.glob('*.json'):
            year_str = f.stem
            try:
                all_years.add(int(year_str))
            except ValueError:
                pass

    for year in sorted(all_years):
        # Try to load standings for this year
        try:
            standings_data = load_standings_year(year)
            if not standings_data:
                continue

            for standing in standings_data:
                pos = standing.get('rank', 0)
                if not pos:
                    continue

                if pos not in position_to_owner_id:
                    position_to_owner_id[pos] = f'owner_{pos:02d}'

                owner_id = position_to_owner_id[pos]

                if owner_id not in owner_map:
                    owner_map[owner_id] = {
                        'standing_position': pos,
                        'names_by_year': {},
                        'all_names': set(),
                    }

                team_name = standing.get('team', '')
                owner_map[owner_id]['names_by_year'][year] = team_name
                owner_map[owner_id]['all_names'].add(team_name)
        except Exception as e:
            # Standings file may not exist for this year
            pass

    # Convert sets to sorted lists
    for owner_id in owner_map:
        owner_map[owner_id]['all_names'] = sorted(list(owner_map[owner_id]['all_names']))

    return owner_map


def save_team_mapping(output_path: Path | None = None) -> Path:
    """Extract and save team identity mapping."""
    if output_path is None:
        output_path = PROCESSED_DIR / 'team_mapping.json'

    owner_map = build_owner_identity_map()

    output = {
        'owner_mapping': owner_map,
        'notes': 'Team names by owner/standing position. Same standing position = same owner across years.',
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)

    return output_path


def load_team_mapping(path: Path | None = None) -> Dict[str, Dict]:
    """Load previously saved team mapping."""
    if path is None:
        path = PROCESSED_DIR / 'team_mapping.json'

    if not path.exists():
        raise FileNotFoundError(f'Team mapping not found: {path}')

    with open(path) as f:
        data = json.load(f)
    return data.get('owner_mapping', {})


def get_owner_by_team_name(team_name: str, mapping: Dict | None = None) -> str | None:
    """Lookup owner ID by team name."""
    if mapping is None:
        mapping = load_team_mapping()

    for owner_id, owner_data in mapping.items():
        if team_name in owner_data.get('all_names', []):
            return owner_id

    return None


def get_team_names_for_owner(owner_id: str, mapping: Dict | None = None) -> Dict[int, str]:
    """Get all name variants for an owner across years."""
    if mapping is None:
        mapping = load_team_mapping()

    if owner_id not in mapping:
        return {}

    return mapping[owner_id].get('names_by_year', {})
