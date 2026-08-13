"""Extract and analyze historical keeper selections from draft history.

Identifies which players each team kept in past drafts.
Keepers = picks in rounds 14-15 (or 15-16 pre-2024).

Also infers keepers by comparing season rosters: players in season N roster
but not drafted in season N+1 draft = inferred keepers.
"""
import json
from pathlib import Path
from typing import Dict, List, Any
from collections import defaultdict
from difflib import SequenceMatcher

from .draft_history import load_draft_years
from .paths import PROCESSED_DIR, RAW_DIR
from .player_registry import normalize_name


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
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2)

    return output_path


def load_keeper_history(path: Path | None = None) -> Dict[str, Dict[int, List[str]]]:
    """Load previously saved keeper history."""
    if path is None:
        path = PROCESSED_DIR / 'keeper_history.json'

    if not path.exists():
        raise FileNotFoundError(f'Keeper history not found: {path}')

    with open(path, encoding='utf-8') as f:
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


def normalize_player_name(name: str) -> str:
    """Normalize player names for matching across sources."""
    return normalize_name(name)


def name_similarity(name1: str, name2: str) -> float:
    """Compute string similarity (0-1) between two player names."""
    n1 = normalize_player_name(name1)
    n2 = normalize_player_name(name2)
    return SequenceMatcher(None, n1, n2).ratio()


def find_player_in_list(player_name: str, player_list: List[str], threshold: float = 0.85) -> bool:
    """Check if player_name appears in player_list (fuzzy match)."""
    for existing_name in player_list:
        if name_similarity(player_name, existing_name) >= threshold:
            return True
    return False


def load_season_rosters() -> Dict[int, Dict[str, List[str]]]:
    """Load season rosters from data files.
    Returns: {season: {team_name: [player1, player2, ...], ...}, ...}"""
    rosters_dir = RAW_DIR / 'season_rosters'
    rosters = {}

    for year_file in sorted(rosters_dir.glob('*.json')):
        year = int(year_file.stem)
        with open(year_file, encoding='utf-8') as f:
            data = json.load(f)

        rosters[year] = {}
        for team_data in data.get('rosters', []):
            team_name = team_data.get('team_name')
            players = [p.get('playerName') for p in team_data.get('players', []) if p.get('playerName')]
            rosters[year][team_name] = players

    return rosters


def load_managers() -> Dict[int, Dict[str, Dict[str, str]]]:
    """Load manager data by year.
    Returns: {year: {team_name: {manager_name, email, ...}, ...}, ...}"""
    managers_dir = RAW_DIR / 'managers'
    managers = {}

    for year_file in sorted(managers_dir.glob('*.json')):
        year = int(year_file.stem)
        with open(year_file, encoding='utf-8') as f:
            data = json.load(f)

        managers[year] = {}
        for manager_data in data.get('managers', []):
            team_name = manager_data.get('team_name')
            managers[year][team_name] = manager_data

    return managers


def infer_keepers_from_rosters() -> Dict[str, Dict[int, List[str]]]:
    """Infer keeper selections by comparing rosters across years.

    Logic: Players in team's season N roster who also appear in season N+1 roster
    but were NOT drafted by team in season N+1 are inferred keepers.

    (This filters out dropped/traded players and captures only retained players.)

    Returns: {team_name: {season: [keeper1, keeper2, ...], ...}, ...}
    """
    rosters = load_season_rosters()
    draft_history = load_draft_years()
    result = defaultdict(dict)

    # For each season from 2021-2024 (look at roster N vs roster N+1 vs draft N+1)
    for season in sorted(rosters.keys()):
        if season >= 2025:  # Can't infer keepers if next season roster not available
            continue

        next_season = season + 1
        if next_season not in draft_history or next_season not in rosters:
            continue

        season_rosters = rosters[season]
        next_season_rosters = rosters[next_season]
        next_draft_picks = draft_history[next_season]

        # Group draft picks by team for season N+1
        draft_picks_by_team = defaultdict(list)
        for pick in next_draft_picks:
            team = pick.get('team', '').strip()
            player = pick.get('playerName', '').strip()
            if team and player:
                draft_picks_by_team[team].append(player)

        # For each team, find players in season N roster who:
        # 1. Also appear in season N+1 roster (retained by team)
        # 2. Were NOT drafted in season N+1 (so they were kept)
        for team_name, roster_players in season_rosters.items():
            if team_name not in next_season_rosters:
                continue

            next_roster_players = next_season_rosters[team_name]
            inferred_keepers = []

            for player in roster_players:
                # Check if player is in next season's roster (retained)
                retained = find_player_in_list(player, next_roster_players)
                if not retained:
                    continue

                # Check if player was drafted in next season
                drafted = find_player_in_list(player, draft_picks_by_team.get(team_name, []))
                if not drafted:
                    inferred_keepers.append(player)

            if inferred_keepers:
                result[team_name][season] = inferred_keepers

    return dict(result)


def augment_managers_with_keeper_history() -> Dict[int, List[Dict[str, Any]]]:
    """Augment manager profiles with historical keeper data.

    Returns: {year: [
        {manager_name, team_name, email, ..., keepers_by_year: {2021: [...], 2022: [...], ...}},
        ...
    ], ...}
    """
    managers = load_managers()
    inferred_keepers = infer_keepers_from_rosters()
    round_based_keepers = extract_keeper_history()

    result = {}

    for year in sorted(managers.keys()):
        result[year] = []

        for team_name, manager_data in managers[year].items():
            augmented = dict(manager_data)

            # Collect inferred keeper history for this team (all years)
            keeper_history = {}
            if team_name in inferred_keepers:
                keeper_history.update(inferred_keepers[team_name])

            # Also include round-based keepers (from draft picks)
            if team_name in round_based_keepers:
                for season, players in round_based_keepers[team_name].items():
                    if season not in keeper_history:
                        keeper_history[season] = players

            if keeper_history:
                augmented['keepers_by_year'] = dict(sorted(keeper_history.items()))

            result[year].append(augmented)

    return result


def save_manager_profiles_with_keepers(output_path: Path | None = None) -> Path:
    """Save augmented manager profiles with keeper history to JSON."""
    if output_path is None:
        output_path = PROCESSED_DIR / 'manager_profiles.json'

    profiles = augment_managers_with_keeper_history()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(profiles, f, indent=2)

    return output_path
