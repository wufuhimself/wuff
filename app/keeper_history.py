"""Extract and analyze historical keeper selections from draft history.

Identifies which players each team kept in past drafts.
Keepers = picks in rounds 14-15 (or 15-16 pre-2024).
"""
import json
from pathlib import Path
from typing import Dict, List, Set, Any
from collections import defaultdict

from .draft_history import load_draft_years
from .paths import PROCESSED_DIR


def get_keeper_rounds(year: int) -> tuple[int, int]:
    """Get keeper rounds for a given draft year.
    2020-2023 had 16 rounds (keeper rounds 15-16).
    2024+ have 15 rounds (keeper rounds 14-15)."""
    if year <= 2023:
        return 15, 16
    return 14, 15


def extract_keepers_by_year() -> Dict[int, Dict[str, List[str]]]:
    """Extract keeper players per team per season.
    Returns: {season: {team: [player1, player2, ...], ...}, ...}"""
    draft_years = load_draft_years()
    result = {}

    for year, picks in draft_years.items():
        keeper_round_min, keeper_round_max = get_keeper_rounds(year)
        keepers_by_team = defaultdict(list)

        for pick in picks:
            if keeper_round_min <= pick.get('round', 0) <= keeper_round_max:
                team = pick.get('team')
                player = pick.get('playerName')
                if team and player:
                    keepers_by_team[team].append(player)

        result[year] = dict(keepers_by_team)

    return result


def extract_keeper_history() -> Dict[str, Dict[int, List[str]]]:
    """Build per-team keeper history across all seasons.
    Returns: {team: {season: [player1, player2, ...], ...}, ...}"""
    keepers_by_year = extract_keepers_by_year()
    result = defaultdict(dict)

    for year, keepers_by_team in keepers_by_year.items():
        for team, players in keepers_by_team.items():
            result[team][year] = players

    return dict(result)


def analyze_keeper_patterns(keeper_history: Dict[str, Dict[int, List[str]]]) -> Dict[str, Any]:
    """Analyze keeper selection patterns per team.
    Returns stats on keeper consistency, position preferences, etc."""
    stats = {}

    for team, seasons in keeper_history.items():
        all_keepers = []
        for year_keepers in seasons.values():
            all_keepers.extend(year_keepers)

        keeper_count = sum(len(k) for k in seasons.values())
        season_count = len(seasons)

        stats[team] = {
            'total_keepers': keeper_count,
            'seasons_kept': season_count,
            'avg_keepers_per_season': round(keeper_count / season_count, 1) if season_count else 0,
            'unique_players_kept': len(set(all_keepers)),
            'seasons': dict(seasons),
        }

    return stats


def save_keeper_history(output_path: Path | None = None) -> Path:
    """Extract and save keeper history to JSON."""
    if output_path is None:
        output_path = PROCESSED_DIR / 'keeper_history.json'

    keeper_history = extract_keeper_history()
    keeper_stats = analyze_keeper_patterns(keeper_history)

    output = {
        'by_team': keeper_history,
        'stats': keeper_stats,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)

    return output_path


def load_keeper_history(path: Path | None = None) -> Dict[str, Dict[int, List[str]]]:
    """Load previously saved keeper history."""
    if path is None:
        path = PROCESSED_DIR / 'keeper_history.json'

    if not path.exists():
        raise FileNotFoundError(f'Keeper history not found: {path}')

    with open(path) as f:
        data = json.load(f)
    return data.get('by_team', {})


def get_team_keeper_strategy(keeper_history: Dict[str, Dict[int, List[str]]], team: str) -> Dict[str, Any]:
    """Analyze a team's keeper strategy from historical data.
    Returns patterns like: "team keeps 2 per year", "prefers RB/WR over QB", etc."""
    if team not in keeper_history:
        return {'found': False}

    seasons = keeper_history[team]
    keeper_counts = [len(players) for players in seasons.values()]

    return {
        'found': True,
        'team': team,
        'seasons_with_data': len(seasons),
        'keeper_counts_by_year': {str(year): len(players) for year, players in seasons.items()},
        'mode_keeper_count': max(set(keeper_counts), key=keeper_counts.count) if keeper_counts else 0,
    }
