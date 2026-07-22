import csv
from pathlib import Path
from typing import Any, Dict, List, Optional

from .paths import (
    PROCESSED_DIR,
    ensure_parent_dir,
)
from .rankings_manager import load_combined_rankings, normalize_player_id
from .draft_history import load_draft_years
from .nfl_stats import load_seasonal_stats, load_rosters


def load_adp_by_season(season: int) -> Dict[str, float]:
    """Compute average draft position (ADP) from draft history for a season.
    Converts (round, pick_in_round) to overall pick number.
    Returns: {normalized_player_id: overall_pick_number}"""
    adp_map = {}

    years = load_draft_years()
    picks = years.get(season, [])

    for pick in picks:
        player_name = pick.get('playerName', '')
        round_num = pick.get('round', 0)
        pick_in_round = pick.get('pick', 0)

        if player_name and round_num and pick_in_round:
            overall_pick = (round_num - 1) * 12 + pick_in_round
            player_id = normalize_player_id(player_name)
            adp_map[player_id] = float(overall_pick)

    return adp_map


def load_player_age_by_season(season: int) -> Dict[str, Optional[int]]:
    """Load player birth years from NFL rosters, compute age for given season.
    Returns: {normalized_player_id: age}"""
    age_map = {}

    rosters = load_rosters(season)
    if not rosters:
        return age_map

    for row in rosters:
        player_name = row.get('player_name', row.get('Player', ''))
        birth_year_str = row.get('birth_year', row.get('Birth Year', ''))

        if player_name and birth_year_str:
            player_id = normalize_player_id(player_name)
            try:
                birth_year = int(birth_year_str)
                age = season - birth_year
                age_map[player_id] = age
            except ValueError:
                pass

    return age_map


def load_bye_weeks(season: int) -> Dict[str, Optional[int]]:
    """Load bye weeks from NFL rosters.
    Returns: {normalized_player_id: bye_week}"""
    bye_map = {}

    rosters = load_rosters(season)
    if not rosters:
        return bye_map

    for row in rosters:
        player_name = row.get('player_name', row.get('Player', ''))
        bye_week = row.get('bye_week', row.get('Bye Week', ''))

        if player_name and bye_week:
            player_id = normalize_player_id(player_name)
            try:
                bye_map[player_id] = int(bye_week)
            except ValueError:
                pass

    return bye_map


def load_season_stats_map(season: int) -> Dict[str, Dict[str, Any]]:
    """Load seasonal stats from nflreadpy, indexed by player ID.
    Returns: {normalized_player_id: {points, position, team, ...}}"""
    stats_map = {}

    stats = load_seasonal_stats(season)
    if not stats:
        return stats_map

    for row in stats:
        player_display_name = row.get('player_display_name', row.get('player_name', row.get('Player', '')))
        player_id_raw = row.get('player_id', row.get('playerID', ''))

        if not player_display_name:
            continue

        player_id = normalize_player_id(player_display_name)

        try:
            points = float(row.get('fantasy_points', row.get('FantasyPoints', 0)))
        except (ValueError, TypeError):
            points = 0.0

        stats_map[player_id] = {
            'playerName': player_display_name,
            'playerIdRaw': player_id_raw,
            'position': row.get('position', row.get('Position', 'UNK')).upper(),
            'team': row.get('team', row.get('Team', 'UNK')).upper(),
            'fantasyPoints': points,
        }

    return stats_map


def build_feature_table(seasons: List[int]) -> List[Dict[str, Any]]:
    """Build feature table joining draft history + stats + rankings + rosters.
    One row per player-season."""
    features = []

    combined_rankings = load_combined_rankings()
    rankings_by_id = {r['playerId']: r for r in combined_rankings}

    for season in seasons:
        print(f'  Building features for {season}...')

        adp_map = load_adp_by_season(season)
        age_map = load_player_age_by_season(season)
        bye_map = load_bye_weeks(season)
        stats_map = load_season_stats_map(season)

        processed = set()

        for player_id, stats in stats_map.items():
            if player_id in processed:
                continue
            processed.add(player_id)

            player_name = stats['playerName']
            ranking_data = rankings_by_id.get(player_id, {})

            adp = adp_map.get(player_id) or adp_map.get(normalize_player_id(player_name))

            draft_round = None
            draft_pick = None
            if adp is not None:
                draft_pick = int(adp)
                draft_round = (draft_pick - 1) // 12 + 1

            age = age_map.get(player_id) or age_map.get(normalize_player_id(player_name))
            bye = bye_map.get(player_id) or bye_map.get(normalize_player_id(player_name))

            feature_row = {
                'player_id': player_id,
                'player_name': player_name,
                'season': season,
                'draft_round': draft_round,
                'draft_pick': draft_pick,
                'adp': adp,
                'position': stats.get('position', 'UNK'),
                'team': stats.get('team', 'UNK'),
                'age': age,
                'bye_week': bye,
                'fantasy_points': stats.get('fantasyPoints', 0),
                'rank_consensus': ranking_data.get('averageRank'),
                'source_count': ranking_data.get('sourceCount', 0),
            }

            source_ranks = ranking_data.get('sourceRanks', {})
            for source, rank in source_ranks.items():
                feature_row[f'rank_{source.lower()}'] = rank

            if adp is not None and ranking_data.get('averageRank') is not None:
                feature_row['rank_vs_adp'] = ranking_data['averageRank'] - adp

            features.append(feature_row)

    return features


def save_feature_table(features: List[Dict[str, Any]], output_path: Optional[Path] = None) -> Path:
    """Save feature table to CSV."""
    if output_path is None:
        output_path = PROCESSED_DIR / 'feature_table.csv'

    if not features:
        raise ValueError('Cannot save empty feature table.')

    ensure_parent_dir(output_path)

    fieldnames = list(features[0].keys())
    fieldnames.sort()

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in features:
            writer.writerow({fn: row.get(fn, '') for fn in fieldnames})

    return output_path


def build_and_save_feature_table(seasons: List[int], output_path: Optional[Path] = None) -> Path:
    """End-to-end: build feature table and save."""
    print(f'Building feature table for seasons {seasons}...')
    features = build_feature_table(seasons)
    print(f'  Generated {len(features)} feature rows')

    print('Saving feature table...')
    output_path = save_feature_table(features, output_path)
    print(f'  Wrote {output_path}')

    return output_path
