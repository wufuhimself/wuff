"""Rank adjustment system for draft board customization.

Applies rules-based shifts to combined rankings (QB-focused: elite passers, rushers, rookies).
Preserves combined rankings as base, exports adjusted + comparison CSV.

Run via: python -m app adjust-rankings
"""
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .nfl_stats import load_seasonal_stats
from .paths import RAW_RANKINGS_DIR, PROCESSED_DIR, ensure_parent_dir
from .rankings_manager import load_combined_rankings
from .draft_history import load_draft_years


def load_adjustment_rules(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load adjustment rules from JSON config."""
    if config_path is None:
        config_path = Path(__file__).parent.parent / 'data' / 'config' / 'board_adjustments.json'

    if not config_path.exists():
        raise FileNotFoundError(f'Board adjustments config not found: {config_path}')

    with open(config_path, 'r') as f:
        return json.load(f)


def load_qb_rushing_yards(season: int = 2025) -> Dict[str, int]:
    """Load QB rushing yards from seasonal NFL stats.
    Falls back to previous season if current season unavailable.
    Returns dict: {player_name_lower: rushing_yards}"""
    stats = load_seasonal_stats(season=season)

    if not stats:
        if season > 2020:
            return load_qb_rushing_yards(season - 1)
        return {}

    qb_rushing = {}

    for row in stats:
        if row.get('position') == 'QB':
            name = row.get('player_display_name', row.get('player_name', '')).strip().lower()
            rushing_yards = row.get('rushing_yards')
            if name and rushing_yards is not None:
                try:
                    qb_rushing[name] = int(rushing_yards)
                except (ValueError, TypeError):
                    pass

    return qb_rushing


def get_rookie_qbs(current_season: int = 2026) -> Set[str]:
    """Identify QBs drafted in current season (rookie class).
    Check draft_history for current year's picks."""
    rookie_set = set()
    draft_years = load_draft_years()

    if current_season in draft_years:
        picks = draft_years[current_season]
        for pick in picks:
            if pick.get('position') == 'QB':
                name = pick.get('playerName', '').strip().lower()
                rookie_set.add(name)

    return rookie_set


def normalize_name(name: str) -> str:
    """Normalize player name for comparison."""
    return name.strip().lower()


def apply_name_rule(
    player: Dict[str, Any],
    rule: Dict[str, Any],
    qb_rushing: Dict[str, int],
    rookie_qbs: Set[str],
) -> Optional[int]:
    """Apply player_name rule if matches."""
    if rule.get('type') != 'player_name':
        return None

    player_name = normalize_name(player['playerName'])
    for rule_name in rule.get('names', []):
        if normalize_name(rule_name) in player_name or player_name in normalize_name(rule_name):
            return rule.get('shift', 0)

    return None


def apply_rookie_qb_rule(
    player: Dict[str, Any],
    rule: Dict[str, Any],
    qb_rushing: Dict[str, int],
    rookie_qbs: Set[str],
) -> Optional[int]:
    """Apply rookie_qb rule if matches."""
    if rule.get('type') != 'rookie_qb':
        return None

    if player.get('position') != 'QB':
        return None

    player_name = normalize_name(player['playerName'])
    if player_name not in rookie_qbs:
        return None

    rushing_min = rule.get('rushing_yards_min', 0)
    rushing_yards = qb_rushing.get(player_name, 0)

    if rushing_yards >= rushing_min:
        return rule.get('shift', 0)

    return None


def apply_position_and_rushing_rule(
    player: Dict[str, Any],
    rule: Dict[str, Any],
    qb_rushing: Dict[str, int],
    rookie_qbs: Set[str],
) -> Optional[int]:
    """Apply position_and_rushing rule if matches."""
    if rule.get('type') != 'position_and_rushing':
        return None

    if player.get('position') != rule.get('position'):
        return None

    rank_below = rule.get('rank_below')
    if rank_below and player.get('ranking', 0) < rank_below:
        return None

    rushing_max = rule.get('rushing_yards_max', float('inf'))
    player_name = normalize_name(player['playerName'])
    rushing_yards = qb_rushing.get(player_name, 0)

    if rushing_yards <= rushing_max:
        return rule.get('shift', 0)

    return None


def apply_rules(
    players: List[Dict[str, Any]],
    rules: List[Dict[str, Any]],
    qb_rushing: Dict[str, int],
    rookie_qbs: Set[str],
) -> List[Dict[str, Any]]:
    """Apply all rules to players, return adjusted rankings."""
    adjusted = []

    for player in players:
        original_rank = player.get('ranking', 0)
        total_shift = 0

        for rule in rules:
            shift = None

            if rule.get('type') == 'player_name':
                shift = apply_name_rule(player, rule, qb_rushing, rookie_qbs)
            elif rule.get('type') == 'rookie_qb':
                shift = apply_rookie_qb_rule(player, rule, qb_rushing, rookie_qbs)
            elif rule.get('type') == 'position_and_rushing':
                shift = apply_position_and_rushing_rule(player, rule, qb_rushing, rookie_qbs)

            if shift is not None:
                total_shift = shift
                break

        adjusted_rank = max(1, original_rank + total_shift)

        adjusted.append({
            **player,
            'adjustedRank': adjusted_rank,
            'rankShift': total_shift,
        })

    adjusted.sort(key=lambda x: (x['adjustedRank'], x['ranking']))

    return adjusted


def export_adjusted_rankings(
    adjusted: List[Dict[str, Any]],
    output_dir: Optional[Path] = None,
) -> tuple[Path, Path]:
    """Export adjusted rankings JSON + comparison CSV.
    Returns (json_path, csv_path)"""
    if output_dir is None:
        output_dir = PROCESSED_DIR

    ensure_parent_dir(output_dir)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M')

    json_path = output_dir / 'rankings_adjusted.json'
    csv_path = output_dir / f'rankings_adjusted_{timestamp}.csv'

    with open(json_path, 'w') as f:
        json.dump(adjusted, f, indent=2)

    with open(csv_path, 'w', newline='') as f:
        fieldnames = ['originalRank', 'adjustedRank', 'rankShift', 'playerName', 'position', 'team']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for player in adjusted:
            writer.writerow({
                'originalRank': player.get('ranking'),
                'adjustedRank': player.get('adjustedRank'),
                'rankShift': player.get('rankShift'),
                'playerName': player.get('playerName'),
                'position': player.get('position'),
                'team': player.get('team'),
            })

    return json_path, csv_path


def adjust_and_export(config_path: Optional[Path] = None) -> tuple[Path, Path]:
    """Main entry point: load rankings + rules, apply adjustments, export."""
    config = load_adjustment_rules(config_path)
    rules = config.get('rules', [])

    rankings = load_combined_rankings()
    if not rankings:
        raise ValueError('No combined rankings found. Run combine-rankings first.')

    qb_rushing = load_qb_rushing_yards()
    rookie_qbs = get_rookie_qbs()

    adjusted = apply_rules(rankings, rules, qb_rushing, rookie_qbs)
    json_path, csv_path = export_adjusted_rankings(adjusted)

    return json_path, csv_path
