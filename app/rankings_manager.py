"""Multi-source rankings combiner.

Loads all JSON/CSV/PDF files from data/raw/rankings/, normalizes, averages ranks.
Outputs data/raw/rankings/rankings_combined.json.
Run via: python -m app combine-rankings
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from .paths import RAW_RANKINGS_DIR, ensure_parent_dir
from .rankings_csv import load_rankings_csv
from .rankings_pdf import load_rankings_pdf


def load_all_rankings() -> List[Dict[str, Any]]:
    """Load all rankings from data/raw/rankings/ (JSON, CSV, and PDF files).
    Returns flat list of player rankings with source attribution."""
    rankings = []

    for file_path in RAW_RANKINGS_DIR.glob('*_rankings.*'):
        if file_path.name == 'rankings_combined.json':
            continue

        source = file_path.stem.replace('_rankings', '')

        if file_path.suffix == '.json':
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for entry in data:
                            entry['source'] = source
                            rankings.append(entry)
            except Exception as e:
                print(f'  Warning: Failed to load {file_path.name}: {e}')

        elif file_path.suffix == '.csv':
            try:
                csv_rankings = load_rankings_csv(file_path, source=source)
                rankings.extend(csv_rankings)
            except Exception as e:
                print(f'  Warning: Failed to load {file_path.name}: {e}')

        elif file_path.suffix == '.pdf':
            try:
                pdf_rankings = load_rankings_pdf(file_path, source=source)
                rankings.extend(pdf_rankings)
            except Exception as e:
                print(f'  Warning: Failed to load {file_path.name}: {e}')

    return rankings


def normalize_player_id(name: str, player_id: Optional[str] = None) -> str:
    """Generate consistent player ID from name if not provided.

    An id generator, not a name normalizer -- and the ids it produces are
    PERSISTED as `playerId` in rankings_combined.json, so it is deliberately
    left off the step 1b collapse onto player_registry.normalize_name.
    Cross-source player identity belongs to the registry now; this only has to
    stay stable with the file it wrote.
    """
    if player_id:
        return str(player_id).strip()
    return name.strip().lower().replace(' ', '_')


def normalize_rankings(rankings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Standardize ranking format across all sources."""
    normalized = []

    for entry in rankings:
        normalized_entry = {
            'playerId': normalize_player_id(
                entry.get('playerName', entry.get('player', '')),
                entry.get('playerId')
            ),
            'playerName': entry.get('playerName', entry.get('player', '')).strip(),
            'position': entry.get('position', 'UNK').upper(),
            'team': entry.get('team', 'UNK').upper(),
            'ranking': int(entry.get('ranking', 0)),
            'source': entry.get('source', 'unknown'),
        }

        if 'posRank' in entry:
            normalized_entry['posRank'] = entry['posRank']

        normalized.append(normalized_entry)

    return normalized


def combine_rankings(rankings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Aggregate rankings by player, averaging across sources.
    Returns sorted list by average rank."""
    by_player: Dict[str, Dict[str, Any]] = {}

    for entry in rankings:
        player_id = entry['playerId']

        if player_id not in by_player:
            by_player[player_id] = {
                'playerId': player_id,
                'playerName': entry['playerName'],
                'position': entry['position'],
                'team': entry['team'],
                'sourceRanks': {},
            }

        by_player[player_id]['sourceRanks'][entry['source']] = entry['ranking']
        if 'posRank' in entry:
            by_player[player_id]['posRank'] = entry['posRank']

    combined = []
    for player_data in by_player.values():
        ranks = list(player_data['sourceRanks'].values())
        avg_rank = sum(ranks) / len(ranks) if ranks else 0
        player_data['averageRank'] = round(avg_rank, 2)
        player_data['ranking'] = round(avg_rank)
        player_data['sourceCount'] = len(ranks)
        combined.append(player_data)

    combined.sort(key=lambda x: x['averageRank'])
    return combined


def save_combined_rankings(combined: List[Dict[str, Any]], output_path: Optional[Path] = None) -> Path:
    """Save combined rankings to JSON file."""
    if output_path is None:
        output_path = RAW_RANKINGS_DIR / 'rankings_combined.json'

    ensure_parent_dir(output_path)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(combined, f, indent=2)

    return output_path


def load_combined_rankings(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load pre-computed combined rankings from JSON."""
    if path is None:
        path = RAW_RANKINGS_DIR / 'rankings_combined.json'

    if not path.exists():
        return []

    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_player_rank(player_name: str, rankings: Optional[List[Dict[str, Any]]] = None) -> Optional[float]:
    """Lookup player's average rank in combined rankings."""
    if rankings is None:
        rankings = load_combined_rankings()

    player_id = normalize_player_id(player_name)

    for entry in rankings:
        if entry['playerId'] == player_id or entry['playerName'].lower() == player_name.lower():
            return entry.get('averageRank')

    return None


def combine_and_save_all() -> Path:
    """End-to-end: load all → normalize → combine → save."""
    print('Loading rankings from all sources...')
    raw = load_all_rankings()
    print(f'  Loaded {len(raw)} total rankings')

    print('Normalizing...')
    normalized = normalize_rankings(raw)

    print('Combining...')
    combined = combine_rankings(normalized)
    print(f'  Combined into {len(combined)} unique players')

    print('Saving combined rankings...')
    output_path = save_combined_rankings(combined)
    print(f'  Wrote {output_path}')

    return output_path
