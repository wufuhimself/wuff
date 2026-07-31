"""Map team identities across seasons via draft slot consistency.

Draft order = inverse of prior season standings (worst record picks 1st, snake each round).
Use this to reliably track teams by slot position across years.
"""
import json
from pathlib import Path
from typing import Dict

from .draft_history import load_draft_years
from .paths import PROCESSED_DIR


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
        except Exception:
            # Standings file may not exist for this year
            pass

    # Convert sets to sorted lists
    for owner_data in owner_map.values():
        owner_data['all_names'] = sorted(owner_data['all_names'])

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
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2)

    return output_path


def load_team_mapping(path: Path | None = None) -> Dict[str, Dict]:
    """Load previously saved team mapping."""
    if path is None:
        path = PROCESSED_DIR / 'team_mapping.json'

    if not path.exists():
        raise FileNotFoundError(f'Team mapping not found: {path}')

    with open(path, encoding='utf-8') as f:
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
