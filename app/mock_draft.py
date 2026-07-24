"""Mock draft simulator: forecasts 2026 draft based on keepers, rankings, position needs, team patterns."""
import json
import csv
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
from collections import defaultdict

from .paths import PROCESSED_DIR, RAW_STANDINGS_DIR, RAW_DRAFT_HISTORY_DIR, RAW_RANKINGS_DIR
from .standings import draft_order_from_standings, snake_draft_order


LEAGUE_STARTERS = {
    'QB': 1,
    'RB': 2,
    'WR': 3,
    'TE': 1,
    'SUPERFLEX': 1,
    'DST': 1,  # Defense/ST
}

SUPERFLEX_ELIGIBLE = {'QB'}
TOP_DEFENSES = {'SF', 'KC', 'BUF', 'DEN', 'BAL', 'TB'}  # Elite defenses worth drafting

# Team name mappings (old draft history name → keeper prediction name)
TEAM_NAME_MAPPING = {
    'more like lamer jackson': 'Wuf',
    'Balls Deep': 'BALLS DEEP',
    'Look at all...': 'Look at all those Pickens',
    'Big Dick Nic...': 'Kiss your Cousins',
}


def normalize_team_name(team: str) -> str:
    """Map old team names from standings to current keeper prediction names."""
    return TEAM_NAME_MAPPING.get(team, team)


def load_keeper_predictions(filepath: Optional[Path] = None) -> Dict[str, List[str]]:
    """Load keeper predictions CSV. Returns {team_name: [keeper1, keeper2]}."""
    if filepath is None:
        filepath = PROCESSED_DIR / 'keeper_predictions_2026.csv'

    if not filepath.exists():
        raise FileNotFoundError(f'Keeper predictions not found: {filepath}')

    keepers = {}
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            team = row.get('Team', '').strip()
            if team:
                keeper_names = [
                    row.get('Keeper 1', '').strip(),
                    row.get('Keeper 2', '').strip(),
                ]
                keepers[team] = [k for k in keeper_names if k]

    return keepers


def load_adjusted_rankings(filepath: Optional[Path] = None) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """Load adjusted rankings JSON. Returns (lookup_dict, rankings_list)."""
    if filepath is None:
        filepath = PROCESSED_DIR / 'rankings_adjusted.json'

    if not filepath.exists():
        filepath = RAW_RANKINGS_DIR / 'rankings_combined.json'

    if not filepath.exists():
        raise FileNotFoundError(f'Rankings not found: {filepath}')

    with open(filepath, 'r') as f:
        rankings = json.load(f)

    # Build lookup by player name (lowercase)
    lookup = {}
    for player in rankings:
        key = player.get('playerName', '').lower().strip()
        if key:
            lookup[key] = player

    return lookup, rankings


def get_draft_order_2026(standings_year: int = 2025) -> List[str]:
    """Derive 2026 draft order from 2025 standings (inverse, snake)."""
    standings_path = RAW_STANDINGS_DIR / f'{standings_year}.json'
    if not standings_path.exists():
        raise FileNotFoundError(f'Standings not found: {standings_path}')

    with open(standings_path, 'r') as f:
        data = json.load(f)

    standings = data.get('standings', [])
    standings = sorted(standings, key=lambda x: x.get('rank', 0))

    # Reverse to get worst-first order, normalize team names
    teams = [normalize_team_name(s.get('team', '')) for s in reversed(standings)]

    # Apply snake order (15 rounds)
    draft_order = []
    for round_num in range(1, 16):
        if round_num % 2 == 1:
            draft_order.extend(teams)
        else:
            draft_order.extend(reversed(teams))

    return draft_order


def build_manager_profiles(rankings_lookup: Dict[str, Dict]) -> Dict[str, Dict[str, Dict[int, float]]]:
    """Build manager personality profiles from historical draft data.
    Returns {team: {position: {round: frequency}}}"""

    profiles = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    draft_history_dir = RAW_DRAFT_HISTORY_DIR

    if not draft_history_dir.exists():
        return profiles

    for year in range(2022, 2026):
        history_file = draft_history_dir / f'{year}.json'
        if not history_file.exists():
            continue

        with open(history_file) as f:
            data = json.load(f)

        picks = data.get('picks', [])
        for pick in picks:
            team = normalize_team_name(pick.get('team', ''))
            round_num = pick.get('round', 0)
            player = pick.get('playerName', '').lower().strip()

            # Look up position from rankings
            player_data = rankings_lookup.get(player)
            if player_data and team:
                pos = player_data.get('position', 'UNK')
                if pos != 'UNK':
                    profiles[team][pos][round_num] += 1

    # Normalize to frequencies
    for team in profiles:
        for pos in profiles[team]:
            total = sum(profiles[team][pos].values())
            if total > 0:
                for round_num in profiles[team][pos]:
                    profiles[team][pos][round_num] /= total

    return dict(profiles)


def build_position_need_scores(team: str, keeper_names: List[str], rankings_lookup: Dict,
                                 taken_this_round: List[str]) -> Dict[str, float]:
    """Score position needs: higher score = more need. Used as tiebreak."""
    keeper_positions = defaultdict(int)

    for keeper_name in keeper_names:
        key = keeper_name.lower().strip()
        if key in rankings_lookup:
            pos = rankings_lookup[key].get('position', 'UNK')
            keeper_positions[pos] += 1

    # Count current coverage
    position_needs = {}
    for pos, slot_count in LEAGUE_STARTERS.items():
        kept = keeper_positions.get(pos, 0)
        taken = sum(1 for p in taken_this_round if rankings_lookup.get(p.lower(), {}).get('position') == pos)
        need = max(0, slot_count - kept - taken)

        # Weight: scarcity + league format
        if pos == 'QB':
            weight = 2.0 if need > 0 else 0
        elif pos == 'SUPERFLEX':
            weight = 1.5 if need > 0 else 0
        elif pos == 'WR':
            weight = 1.3 if need > 0 else 0
        elif pos == 'RB':
            weight = 1.2 if need > 0 else 0
        elif pos == 'TE':
            weight = 1.0 if need > 0 else 0
        elif pos == 'DST':
            weight = 0.5 if need > 0 else 0
        else:
            weight = 0

        position_needs[pos] = need * weight

    return position_needs


def score_pick(
    player: Dict[str, Any],
    team: str,
    round_num: int,
    position_needs: Dict[str, float],
    manager_profiles: Dict[str, Dict[str, Dict[int, float]]],
    te_taken_by_team: bool,
    def_ranking: int,
) -> float:
    """Score a player for this team/round. Higher = better pick."""

    pos = player.get('position', 'UNK')
    rank = player.get('adjustedRank', player.get('ranking', 999))

    # Primary: inverse of rank (lower rank = higher score), 0-100 scale
    rank_score = max(0, 100 - rank)

    # Tiebreak 1: position need
    pos_need = position_needs.get(pos, 0)

    # Tiebreak 2: team's historical preference for this position in this round
    team_profile = manager_profiles.get(team, {})
    pos_profile = team_profile.get(pos, {})
    team_pref = pos_profile.get(round_num, 0.0)

    # Tiebreak 3: TE enforcement (if no TE yet by round 6, boost TE heavily)
    te_boost = 0
    if round_num <= 6 and not te_taken_by_team and pos == 'TE':
        te_boost = 15.0  # Significant boost to force TE pick

    # Tiebreak 4: Elite defenses in late rounds (round 12+)
    def_boost = 0
    if round_num >= 12 and pos == 'DST':
        nfl_team = player.get('team', '')
        if nfl_team in TOP_DEFENSES:
            def_boost = 8.0  # Boost for elite defenses late

    composite = rank_score + (pos_need * 2.0) + (team_pref * 3.0) + te_boost + def_boost
    return composite


def simulate_draft(
    keeper_predictions: Dict[str, List[str]],
    rankings_lookup: Dict,
    rankings_all: List[Dict],
    draft_order: List[str],
    manager_profiles: Dict[str, Dict[str, Dict[int, float]]],
) -> List[Dict[str, Any]]:
    """Simulate 15-round draft. Returns list of picks."""

    # Track which players have been taken
    taken_players = set()
    taken_by_team = defaultdict(list)
    te_taken_by_team = defaultdict(bool)

    # Seed taken players with keepers (will be in rounds 14-15, but block early drafting)
    keepers_to_add = {}
    for team, keeper_names in keeper_predictions.items():
        for keeper_name in keeper_names:
            key = keeper_name.lower().strip()
            keeper_player = rankings_lookup.get(key)
            if keeper_player:
                # Block keeper from being drafted in normal rounds
                taken_players.add(key)
                if team not in keepers_to_add:
                    keepers_to_add[team] = []
                keepers_to_add[team].append(keeper_player)

    mock_picks = []

    # 180 picks total (15 rounds x 12 teams)
    for pick_num in range(1, 181):
        round_num = (pick_num - 1) // 12 + 1
        team_idx = (pick_num - 1) % 12
        team = draft_order[team_idx]

        # Check if this is a keeper round for this team
        is_keeper_round = round_num in [14, 15]
        if is_keeper_round and team in keepers_to_add and keepers_to_add[team]:
            # Auto-pick keeper
            keeper = keepers_to_add[team].pop(0)
            player_key = keeper.get('playerName', '').lower().strip()
            taken_players.add(player_key)
            taken_by_team[team].append(player_key)

            mock_picks.append({
                'round': round_num,
                'pick': pick_num,
                'pickInRound': (pick_num - 1) % 12 + 1,
                'team': team,
                'playerName': keeper.get('playerName', ''),
                'position': keeper.get('position', 'UNK'),
                'rank': keeper.get('adjustedRank', keeper.get('ranking', 0)),
                'nflTeam': keeper.get('team', ''),
                'isKeeper': True,
            })

            if keeper.get('position') == 'TE':
                te_taken_by_team[team] = True

            continue

        # Regular draft pick: calculate needs + preferences
        position_needs = build_position_need_scores(team, keeper_predictions.get(team, []), rankings_lookup, taken_by_team[team])

        # Find best available player
        best_player = None
        best_score = -1

        for player in rankings_all:
            player_key = player.get('playerName', '').lower().strip()

            # Skip if already taken
            if player_key in taken_players:
                continue

            def_ranking = player.get('ranking', 999)
            score = score_pick(player, team, round_num, position_needs, manager_profiles,
                              te_taken_by_team[team], def_ranking)

            if score > best_score:
                best_score = score
                best_player = player

        if best_player:
            player_key = best_player.get('playerName', '').lower().strip()
            taken_players.add(player_key)
            taken_by_team[team].append(player_key)

            pos = best_player.get('position', 'UNK')
            if pos == 'TE':
                te_taken_by_team[team] = True

            mock_picks.append({
                'round': round_num,
                'pick': pick_num,
                'pickInRound': (pick_num - 1) % 12 + 1,
                'team': team,
                'playerName': best_player.get('playerName', ''),
                'position': pos,
                'rank': best_player.get('adjustedRank', best_player.get('ranking', 0)),
                'nflTeam': best_player.get('team', ''),
                'isKeeper': False,
            })

    return mock_picks


def run_mock_draft() -> List[Dict[str, Any]]:
    """End-to-end: load data, simulate draft, return picks."""
    keeper_predictions = load_keeper_predictions()
    rankings_lookup, rankings_all = load_adjusted_rankings()
    draft_order = get_draft_order_2026()
    manager_profiles = build_manager_profiles(rankings_lookup)

    mock_picks = simulate_draft(keeper_predictions, rankings_lookup, rankings_all, draft_order, manager_profiles)
    return mock_picks


def export_mock_draft(picks: List[Dict[str, Any]], output_dir: Optional[Path] = None) -> Path:
    """Export mock draft to CSV."""
    if output_dir is None:
        output_dir = PROCESSED_DIR

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / 'mock_draft_2026.csv'

    with open(csv_path, 'w', newline='') as f:
        fieldnames = ['round', 'pickInRound', 'team', 'playerName', 'position', 'rank', 'nflTeam', 'isKeeper']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for pick in picks:
            writer.writerow({
                'round': pick['round'],
                'pickInRound': pick['pickInRound'],
                'team': pick['team'],
                'playerName': pick['playerName'],
                'position': pick['position'],
                'rank': pick['rank'],
                'nflTeam': pick['nflTeam'],
                'isKeeper': pick.get('isKeeper', False),
            })

    return csv_path
