"""Mock draft simulator: forecasts 2026 draft based on keepers, rankings, position needs, team patterns."""
import json
import csv
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
from collections import defaultdict

from .paths import PROCESSED_DIR, RAW_STANDINGS_DIR, RAW_DRAFT_HISTORY_DIR, RAW_RANKINGS_DIR, RAW_DRAFT_PICKS_DIR


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

# Draft position limits: teams won't exceed these, but still draft BPA (best player available)
# If a team hits a position limit, that position's players get heavy scoring penalty,
# making the next-best available player (different position) more attractive.
POSITION_LIMITS = {
    'QB': 3,   # Cover starter + SUPERFLEX depth
    'RB': 7,   # No hard limit (but penalized past 7)
    'WR': 7,   # No hard limit (but penalized past 7)
    'TE': 2,   # Cover starter + backup
    'DST': 1,  # Defense starter only
    'K': 0,    # Never draft kicker (league dropped K in 2024)
}


def load_current_teams(filepath: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    """Load keeper predictions as canonical team source.
    Returns {current_team_name: {manager, keeper1, keeper2}}"""
    if filepath is None:
        filepath = PROCESSED_DIR / 'keeper_predictions_2026.csv'

    if not filepath.exists():
        return {}

    teams = {}
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            team = row.get('Team', '').strip()
            if team:
                teams[team] = {
                    'manager': row.get('Manager', '').strip(),
                    'keeper1': row.get('Keeper 1', '').strip(),
                    'keeper2': row.get('Keeper 2', '').strip(),
                }
    return teams


def load_adjusted_rankings(filepath: Optional[Path] = None) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """Load adjusted rankings JSON. Returns (lookup_dict, rankings_list)."""
    if filepath is None:
        filepath = PROCESSED_DIR / 'rankings_adjusted.json'

    if not filepath.exists():
        filepath = RAW_RANKINGS_DIR / 'rankings_combined.json'

    if not filepath.exists():
        raise FileNotFoundError(f'Rankings not found: {filepath}')

    with open(filepath, 'r', encoding='utf-8') as f:
        rankings = json.load(f)

    # Build lookup by player name (lowercase)
    lookup = {}
    for player in rankings:
        key = player.get('playerName', '').lower().strip()
        if key:
            lookup[key] = player

    return lookup, rankings


def get_draft_order_2026(standings_year: int = 2025, current_teams: Optional[Dict] = None) -> List[str]:
    """Derive 2026 draft order from standings (inverse, snake).
    Uses current_teams to normalize standing team names to keeper prediction names."""
    standings_path = RAW_STANDINGS_DIR / f'{standings_year}.json'
    if not standings_path.exists():
        raise FileNotFoundError(f'Standings not found: {standings_path}')

    if current_teams is None:
        current_teams = load_current_teams()

    with open(standings_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    standings = data.get('standings', [])
    standings = sorted(standings, key=lambda x: x.get('rank', 0))

    # Map standings team names to keeper_predictions team names
    # Standings may use old names (e.g., "more like lamer jackson" for "Wuf")
    current_team_names = set(current_teams.keys())

    def normalize_team(standings_team: str) -> str:
        """Map standings team name to current name. Check standing notes for renames."""
        if standings_team in current_team_names:
            return standings_team
        # Check if standings entry has a note about team rename
        for entry in standings:
            if entry.get('team') == standings_team:
                note = entry.get('note', '')
                if 'now displayed as' in note:
                    # Extract new name from note (e.g., "This is the team now displayed as 'Wuf'.")
                    import re
                    match = re.search(r"displayed as '([^']+)'", note)
                    if match:
                        new_name = match.group(1)
                        if new_name in current_team_names:
                            return new_name
        return standings_team

    # Reverse to get worst-first order, normalize team names
    teams = [normalize_team(s.get('team', '')) for s in reversed(standings)]

    # Apply snake order (15 rounds)
    draft_order = []
    for round_num in range(1, 16):
        if round_num % 2 == 1:
            draft_order.extend(teams)
        else:
            draft_order.extend(reversed(teams))

    return draft_order


def get_draft_order_2026_with_trades(standings_year: int = 2025, current_teams: Optional[Dict] = None) -> List[str]:
    """Build draft order accounting for traded picks.
    Reads picksByRound from draft_picks.json to determine how many picks each team has each round,
    then respects snake order from standings."""

    # Load draft picks to see who owns what
    picks_path = RAW_DRAFT_PICKS_DIR / '2026.json'
    if not picks_path.exists():
        # Fallback to standings-based order if no trades file
        return get_draft_order_2026(standings_year=standings_year, current_teams=current_teams)

    with open(picks_path, encoding='utf-8') as f:
        picks_data = json.load(f)

    # Get standings order (draft order is reverse standings: worst to best)
    standings_file = RAW_STANDINGS_DIR / f'{standings_year}.json'
    if not standings_file.exists():
        raise FileNotFoundError(f'Standings not found: {standings_file}')

    with open(standings_file, encoding='utf-8') as f:
        standings_data = json.load(f)

    standings = sorted(standings_data.get('standings', []), key=lambda x: x.get('rank', 0))

    # Build standings team name -> normalized name mapping
    standings_to_normalized = {}
    for entry in standings:
        standings_team = entry.get('team', '')
        standings_to_normalized[standings_team] = standings_team
        # Check for rename note
        note = entry.get('note', '')
        if 'now displayed as' in note:
            import re
            match = re.search(r"displayed as '([^']+)'", note)
            if match:
                new_name = match.group(1)
                standings_to_normalized[standings_team] = new_name

    # Reverse for draft order (worst team picks first)
    team_order = [standings_to_normalized.get(s.get('team', ''), s.get('team', '')) for s in reversed(standings)]

    # Build lookup: team_name -> picksByRound
    team_picks = {}
    for team_entry in picks_data.get('teams', []):
        team_name = team_entry.get('teamName', '')
        picks_by_round = {int(k): v for k, v in team_entry.get('picksByRound', {}).items()}
        team_picks[team_name] = picks_by_round

    # Build actual draft order accounting for traded picks
    draft_order = []

    # For each round, add teams in snake order, respecting their pick count
    for round_num in range(1, 16):
        # Determine team order for this round (snake pattern)
        if round_num % 2 == 1:
            # Odd rounds: forward order
            round_team_order = team_order
        else:
            # Even rounds: reverse order
            round_team_order = list(reversed(team_order))

        # Add teams according to their pick count this round
        for team in round_team_order:
            num_picks = team_picks.get(team, {}).get(round_num, 0)
            for _ in range(num_picks):
                draft_order.append(team)

    return draft_order


def build_manager_profiles(rankings_lookup: Dict[str, Dict]) -> Dict[str, Dict[str, Dict[int, float]]]:
    """Build manager personality profiles from historical draft data (2022-2025).
    Returns {team_name: {position: {round: frequency}}}
    Note: profiles keyed by historical team names; caller should match current teams if needed."""

    profiles = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    draft_history_dir = RAW_DRAFT_HISTORY_DIR

    if not draft_history_dir.exists():
        return profiles

    for year in range(2022, 2026):
        history_file = draft_history_dir / f'{year}.json'
        if not history_file.exists():
            continue

        with open(history_file, encoding='utf-8') as f:
            data = json.load(f)

        picks = data.get('picks', [])
        for pick in picks:
            team = pick.get('team', '')
            round_num = pick.get('round', 0)
            player = pick.get('playerName', '').lower().strip()

            if not team:
                continue

            # Look up position from rankings
            player_data = rankings_lookup.get(player)
            if player_data:
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


def build_position_need_scores(keeper_names: List[str], rankings_lookup: Dict,
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
    *,
    manager_profiles: Dict[str, Dict[str, Dict[int, float]]],
    te_taken_by_team: bool,
    position_counts: Dict[str, int],
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
        te_boost = 15.0

    # Tiebreak 4: Elite defenses in late rounds (round 12+)
    def_boost = 0
    if round_num >= 12 and pos == 'DST':
        nfl_team = player.get('team', '')
        if nfl_team in TOP_DEFENSES:
            def_boost = 8.0

    # Position limit penalties: penalize positions team has maxed out.
    # This makes maxed-out positions unattractive, so BPA shifts to available positions.
    # E.g., if team at 3 QBs, a rank-10 QB gets -80 penalty → rank-20 RB looks better.
    position_penalty = 0
    pos_limit = POSITION_LIMITS.get(pos, 999)
    pos_count = position_counts.get(pos, 0)

    if pos == 'K':
        # Never draft kicker (league has no K slot)
        position_penalty = -1000
    elif pos_count >= pos_limit:
        # Team at or over limit: heavy penalty (discourages this position)
        position_penalty = -80 - (pos_count - pos_limit) * 30
    elif pos_count == pos_limit - 1:
        # Team at limit-1: medium penalty (warns away from this position)
        position_penalty = -25
    elif pos_count >= pos_limit - 2 and pos in ['QB', 'TE']:
        # Key positions: soft penalty approaching limit
        position_penalty = -5

    composite = rank_score + (pos_need * 2.0) + (team_pref * 3.0) + te_boost + def_boost + position_penalty
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
    position_counts = defaultdict(lambda: defaultdict(int))  # {team: {position: count}}

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

    # 180 picks total (15 rounds x 12 teams, though with trades some teams have multiple per round)
    for pick_num in range(1, 181):
        round_num = (pick_num - 1) // 12 + 1
        # With trades, draft_order is a sequential list, not a repeating 12-team pattern
        team = draft_order[pick_num - 1]

        # Check if this is a keeper round for this team
        is_keeper_round = round_num in [14, 15]
        if is_keeper_round and team in keepers_to_add and keepers_to_add[team]:
            # Auto-pick keeper
            keeper = keepers_to_add[team].pop(0)
            player_key = keeper.get('playerName', '').lower().strip()
            taken_players.add(player_key)
            taken_by_team[team].append(player_key)

            keeper_pos = keeper.get('position', 'UNK')
            mock_picks.append({
                'round': round_num,
                'pick': pick_num,
                'pickInRound': (pick_num - 1) % 12 + 1,
                'team': team,
                'playerName': keeper.get('playerName', ''),
                'position': keeper_pos,
                'rank': keeper.get('adjustedRank', keeper.get('ranking', 0)),
                'nflTeam': keeper.get('team', ''),
                'isKeeper': True,
            })

            if keeper_pos != 'UNK':
                position_counts[team][keeper_pos] += 1

            if keeper_pos == 'TE':
                te_taken_by_team[team] = True

            continue

        # Regular draft pick: calculate needs + preferences
        position_needs = build_position_need_scores(keeper_predictions.get(team, []), rankings_lookup, taken_by_team[team])

        # Find best available player
        best_player = None
        best_score = -1

        for player in rankings_all:
            player_key = player.get('playerName', '').lower().strip()

            # Skip if already taken
            if player_key in taken_players:
                continue

            score = score_pick(
                player, team, round_num, position_needs,
                manager_profiles=manager_profiles,
                te_taken_by_team=te_taken_by_team[team],
                position_counts=position_counts[team],
            )

            if score > best_score:
                best_score = score
                best_player = player

        if best_player:
            player_key = best_player.get('playerName', '').lower().strip()
            taken_players.add(player_key)
            taken_by_team[team].append(player_key)

            pos = best_player.get('position', 'UNK')

            if pos != 'UNK':
                position_counts[team][pos] += 1

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
    current_teams = load_current_teams()
    keeper_predictions = {team: [v['keeper1'], v['keeper2']] for team, v in current_teams.items()}
    rankings_lookup, rankings_all = load_adjusted_rankings()
    # Use trade-aware draft order
    draft_order = get_draft_order_2026_with_trades(current_teams=current_teams)
    manager_profiles = build_manager_profiles(rankings_lookup)

    mock_picks = simulate_draft(keeper_predictions, rankings_lookup, rankings_all, draft_order, manager_profiles)
    return mock_picks


def export_mock_draft(picks: List[Dict[str, Any]], output_dir: Optional[Path] = None) -> Path:
    """Export mock draft to CSV."""
    if output_dir is None:
        output_dir = PROCESSED_DIR

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / 'mock_draft_2026.csv'

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
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
