import csv
from pathlib import Path
from typing import Any, Dict, List, Optional
from .paths import PROCESSED_DIR


def load_feature_table(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load feature table from CSV."""
    if path is None:
        path = PROCESSED_DIR / 'feature_table.csv'

    if not path.exists():
        return []

    rows = []
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    return rows


def convert_numeric(value: str, default: Optional[float] = None) -> Optional[float]:
    """Convert CSV string to float, return default if empty or non-numeric."""
    if not value or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def find_gems(feature_rows: List[Dict[str, Any]], season: int = 2025, adp_window: int = 10) -> List[Dict[str, Any]]:
    """Find gems: players who outperformed same-position peers at similar draft positions.
    Compares each player to average of their position drafted within ±adp_window picks.
    Excludes keeper rounds (14-15)."""

    all_rows = feature_rows
    season_rows = [r for r in all_rows if int(r.get('season', 0)) == season]

    pos_adp_to_points: Dict[str, Dict[int, List[float]]] = {}
    drafted_players_this_season = []

    for row in season_rows:
        adp = convert_numeric(row.get('adp'))
        draft_round = convert_numeric(row.get('draft_round'))
        fantasy_points = convert_numeric(row.get('fantasy_points'), 0)
        position = row.get('position', 'UNK')

        if adp is None or fantasy_points < 30 or (draft_round and draft_round >= 14):
            continue

        drafted_players_this_season.append(row)

    for row in all_rows:
        adp = convert_numeric(row.get('adp'))
        draft_round = convert_numeric(row.get('draft_round'))
        fantasy_points = convert_numeric(row.get('fantasy_points'), 0)
        position = row.get('position', 'UNK')

        if adp is None or fantasy_points < 30 or (draft_round and draft_round >= 14):
            continue

        adp_int = int(adp)
        if position not in pos_adp_to_points:
            pos_adp_to_points[position] = {}
        if adp_int not in pos_adp_to_points[position]:
            pos_adp_to_points[position][adp_int] = []
        pos_adp_to_points[position][adp_int].append(fantasy_points)

    gems = []
    for row in drafted_players_this_season:
        adp = convert_numeric(row.get('adp'))
        fantasy_points = convert_numeric(row.get('fantasy_points'), 0)
        position = row.get('position', 'UNK')
        adp_int = int(adp)

        pos_data = pos_adp_to_points.get(position, {})
        window_points = []
        for slot_adp in range(adp_int - adp_window, adp_int + adp_window + 1):
            if slot_adp in pos_data:
                window_points.extend(pos_data[slot_adp])

        if not window_points:
            continue

        expected = sum(window_points) / len(window_points)
        overperformance = fantasy_points - expected

        if overperformance > 0:
            gems.append({
                'playerName': row.get('player_name', 'N/A'),
                'position': position,
                'team': row.get('team', 'UNK'),
                'season': int(row.get('season', 0)),
                'adp': adp,
                'fantasyPoints': fantasy_points,
                'expected': round(expected, 1),
                'overperformance': round(overperformance, 1),
            })

    gems.sort(key=lambda x: x['overperformance'], reverse=True)
    return gems[:50]


def find_busts(feature_rows: List[Dict[str, Any]], season: int = 2025, adp_window: int = 10) -> List[Dict[str, Any]]:
    """Find busts: players who underperformed same-position peers at similar draft positions.
    Compares each player to average of their position drafted within ±adp_window picks.
    Excludes keeper rounds (14-15)."""

    all_rows = feature_rows
    season_rows = [r for r in all_rows if int(r.get('season', 0)) == season]

    pos_adp_to_points: Dict[str, Dict[int, List[float]]] = {}
    drafted_players_this_season = []

    for row in season_rows:
        adp = convert_numeric(row.get('adp'))
        draft_round = convert_numeric(row.get('draft_round'))
        fantasy_points = convert_numeric(row.get('fantasy_points'), 0)
        position = row.get('position', 'UNK')

        if adp is None or fantasy_points < 30 or (draft_round and draft_round >= 14):
            continue

        drafted_players_this_season.append(row)

    for row in all_rows:
        adp = convert_numeric(row.get('adp'))
        draft_round = convert_numeric(row.get('draft_round'))
        fantasy_points = convert_numeric(row.get('fantasy_points'), 0)
        position = row.get('position', 'UNK')

        if adp is None or fantasy_points < 30 or (draft_round and draft_round >= 14):
            continue

        adp_int = int(adp)
        if position not in pos_adp_to_points:
            pos_adp_to_points[position] = {}
        if adp_int not in pos_adp_to_points[position]:
            pos_adp_to_points[position][adp_int] = []
        pos_adp_to_points[position][adp_int].append(fantasy_points)

    busts = []
    for row in drafted_players_this_season:
        adp = convert_numeric(row.get('adp'))
        fantasy_points = convert_numeric(row.get('fantasy_points'), 0)
        position = row.get('position', 'UNK')
        adp_int = int(adp)

        pos_data = pos_adp_to_points.get(position, {})
        window_points = []
        for slot_adp in range(adp_int - adp_window, adp_int + adp_window + 1):
            if slot_adp in pos_data:
                window_points.extend(pos_data[slot_adp])

        if not window_points:
            continue

        expected = sum(window_points) / len(window_points)
        underperformance = expected - fantasy_points

        if underperformance > 0:
            busts.append({
                'playerName': row.get('player_name', 'N/A'),
                'position': position,
                'team': row.get('team', 'UNK'),
                'season': int(row.get('season', 0)),
                'adp': adp,
                'fantasyPoints': fantasy_points,
                'expected': round(expected, 1),
                'underperformance': round(underperformance, 1),
            })

    busts.sort(key=lambda x: x['underperformance'], reverse=True)
    return busts[:50]


def analyze_position(feature_rows: List[Dict[str, Any]], season: int = 2025) -> Dict[str, Dict[str, Any]]:
    """Analyze average performance by position."""
    pos_stats: Dict[str, Dict[str, Any]] = {}

    for row in feature_rows:
        if int(row.get('season', 0)) != season:
            continue

        position = row.get('position', 'UNK')
        fantasy_points = convert_numeric(row.get('fantasy_points'), 0)
        adp = convert_numeric(row.get('adp'))

        if fantasy_points < 50 or adp is None:
            continue

        if position not in pos_stats:
            pos_stats[position] = {'totalPoints': 0, 'count': 0, 'avgPoints': 0, 'avgAdp': 0, 'adps': []}

        pos_stats[position]['totalPoints'] += fantasy_points
        pos_stats[position]['count'] += 1
        pos_stats[position]['adps'].append(adp)

    for pos in pos_stats:
        data = pos_stats[pos]
        data['avgPoints'] = round(data['totalPoints'] / data['count'], 2) if data['count'] > 0 else 0
        data['avgAdp'] = round(sum(data['adps']) / len(data['adps']), 2) if data['adps'] else 0
        del data['adps']
        del data['totalPoints']

    return dict(sorted(pos_stats.items()))


def load_and_analyze(season: int = 2025) -> Dict[str, Any]:
    """Load feature table and run all analysis."""
    rows = load_feature_table()
    if not rows:
        return {'error': 'Feature table not found. Run `build-features` first.'}

    gems = find_gems(rows, season)
    busts = find_busts(rows, season)
    positions = analyze_position(rows, season)

    gems_by_pos = {}
    for gem in gems:
        pos = gem.get('position', 'UNK')
        if pos not in gems_by_pos:
            gems_by_pos[pos] = []
        gems_by_pos[pos].append(gem)

    for pos in gems_by_pos:
        for rank, gem in enumerate(gems_by_pos[pos], start=1):
            gem['rank'] = rank

    busts_by_pos = {}
    for bust in busts:
        pos = bust.get('position', 'UNK')
        if pos not in busts_by_pos:
            busts_by_pos[pos] = []
        busts_by_pos[pos].append(bust)

    for pos in busts_by_pos:
        for rank, bust in enumerate(busts_by_pos[pos], start=1):
            bust['rank'] = rank

    return {
        'season': season,
        'gems': gems,
        'gems_by_pos': gems_by_pos,
        'busts': busts,
        'busts_by_pos': busts_by_pos,
        'positions': positions,
        'totalPlayers': len([r for r in rows if int(r.get('season', 0)) == season]),
    }
