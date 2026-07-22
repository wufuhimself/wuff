"""Average Draft Position (ADP) loader and value analysis.

ADP = where players actually go in real drafts (market consensus).
Compare against expert rankings to find overrated/underrated players.

Workflow:
1. Save ADP CSV to data/raw/adp/fantasypros_adp.csv
2. Load combined rankings: python -m app combine-rankings
3. Run value analysis: from app.adp_manager import rank_vs_adp_analysis
"""
import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .paths import RAW_ADP_DIR, RAW_RANKINGS_DIR, ensure_parent_dir


def normalize_player_name(name: str) -> str:
    """Normalize player name for matching across sources.
    Handles: 'Player Name', 'Player Name (Bye)', 'Player Name Team'."""
    # Remove bye week in parens
    name = name.split('(')[0].strip()
    # Remove team abbrev (2-3 caps at end), but preserve Roman numerals (I, II, III, IV, V)
    import re
    # Check if ends with Roman numeral - if so, don't remove it
    if re.search(r'\s+(I{1,3}|IV|VI{0,3}|IX)\s*$', name):
        return name.lower()
    # Otherwise remove trailing team abbrev
    name = re.sub(r'\s+[A-Z]{2,3}\s*$', '', name).strip()
    return name.lower()


def load_adp_csv(csv_path: Path) -> List[Dict[str, Any]]:
    """Load ADP from CSV. Auto-detects Overall/AVG/ADP columns.

    Expected columns: Player, Overall/AVG/ADP, and optional platform-specific columns.
    Returns normalized list of {playerName, adp, sources}.
    """
    players = []

    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError('CSV must have header row')

        for row in reader:
            # Extract player name (may include bye week in parens)
            player_raw = row.get('Player') or row.get('Player (Bye)') or ''
            if not player_raw:
                continue

            player_name = normalize_player_name(player_raw)
            if not player_name:
                continue

            # Find ADP column (look for Overall, AVG, ADP)
            adp_str = None
            for col in ['Overall', 'AVG', 'ADP']:
                if col in row:
                    adp_str = row[col]
                    break

            if not adp_str:
                continue

            try:
                adp = float(adp_str)
            except (ValueError, TypeError):
                continue

            # Collect platform-specific ADPs if available
            platforms = {}
            for platform in ['Sleeper', 'FFPC', 'OP']:
                if platform in row and row[platform]:
                    try:
                        platforms[platform.lower()] = float(row[platform])
                    except (ValueError, TypeError):
                        pass

            players.append({
                'playerName': player_name,
                'adp': adp,
                'platforms': platforms,
                'original': player_raw,
            })

    return sorted(players, key=lambda x: x['adp'])


def save_adp_json(adp_data: List[Dict[str, Any]], output_path: Optional[Path] = None) -> Path:
    """Save normalized ADP to JSON."""
    if output_path is None:
        output_path = RAW_ADP_DIR / 'adp_combined.json'

    ensure_parent_dir(output_path)
    with open(output_path, 'w') as f:
        json.dump(adp_data, f, indent=2)

    return output_path


def load_adp_json(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load pre-computed ADP from JSON."""
    if path is None:
        path = RAW_ADP_DIR / 'adp_combined.json'

    if not path.exists():
        return []

    with open(path, 'r') as f:
        return json.load(f)


def rank_vs_adp_analysis(
    rankings: List[Dict[str, Any]],
    adp_data: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Compare rankings vs ADP to find value plays.

    Returns list of players with rank, adp, and value analysis.
    Positive delta = ranked higher than draft position (undervalued by market).
    Negative delta = ranked lower than draft position (overvalued by market).
    """
    adp_by_name = {p['playerName']: p for p in adp_data}

    analysis = []
    for entry in rankings:
        player_name = normalize_player_name(entry.get('playerName', ''))
        adp_entry = adp_by_name.get(player_name)

        if not adp_entry:
            continue

        rank = entry.get('ranking', 999)
        adp = adp_entry['adp']
        delta = adp - rank  # positive = undervalued, negative = overvalued

        analysis.append({
            'playerName': entry.get('playerName'),
            'position': entry.get('position'),
            'team': entry.get('team'),
            'rank': rank,
            'adp': adp,
            'delta': round(delta, 2),
            'value': 'undervalued' if delta > 5 else 'overvalued' if delta < -5 else 'fair',
            'sources': entry.get('sourceCount', 0),
        })

    # Sort by delta (biggest undervalued first)
    analysis.sort(key=lambda x: x['delta'], reverse=True)
    return analysis


def import_and_save_adp(csv_path: Path, output_path: Optional[Path] = None) -> Path:
    """End-to-end: load CSV → normalize → save JSON."""
    print(f'Loading ADP from {csv_path}...')
    adp_data = load_adp_csv(csv_path)
    print(f'  Loaded {len(adp_data)} players')

    print('Saving normalized ADP...')
    output = save_adp_json(adp_data, output_path)
    print(f'  Wrote {output}')

    return output
