"""Mock draft simulator: forecasts a draft from keepers, rankings, position needs, team patterns.

Per-league since 2026-08-11 (Phase 3 port, docs/roadmap.md). Team count, round
count, keeper-slot rounds, starter slots and position limits all come from the
league's LeagueFormat (app/league_context.py) rather than the frank-gore
constants they used to be hardcoded to; historical data comes through a
repository (app/repository.py). Entry point: run_mock_draft(repo=..., league_format=...),
both defaulting to the default league so old callers are unchanged.

Position limits are derived from the league's own starter slots rather than
being a fixed table -- a 3-WR superflex league and a 2-WR single-QB league
should not draft to the same roster shape.
"""
import json
import csv
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
from collections import defaultdict

from .league_context import LeagueFormat, load_league_format
from .paths import PROCESSED_DIR, RAW_RANKINGS_DIR
from .repository import LeagueDataRepository, get_repository

SUPERFLEX_ELIGIBLE = {'QB'}
TOP_DEFENSES = {'SF', 'KC', 'BUF', 'DEN', 'BAL', 'TB'}  # Elite defenses worth drafting

# Bench depth allowed beyond a position's starter slots before the scoring
# function starts penalizing more of it. Tuned against the frank-gore board;
# they're roster-construction preferences, not league rules, so they stay
# shared across leagues while the starter counts they build on don't.
_BENCH_DEPTH = {'QB': 1, 'RB': 5, 'WR': 4, 'TE': 1, 'DST': 0, 'K': 0}


def position_limits_for(league_format: LeagueFormat) -> Dict[str, int]:
    """How many of each position a team will roster before heavy penalty.

    starter slots (+ SUPERFLEX for QB) + bench depth. Replaces the old fixed
    POSITION_LIMITS table, which encoded frank-gore's 1QB/2RB/3WR/1TE/superflex
    shape and would draft a wrong-shaped roster for any other league.
    K is always 0 in leagues with no K slot -- never draft a kicker there."""
    limits = {}
    for pos, bench in _BENCH_DEPTH.items():
        starters = league_format.slot_count('DEF' if pos == 'DST' else pos)
        if pos == 'QB':
            starters += league_format.slot_count('SUPERFLEX')
        limits[pos] = (starters + bench) if starters else 0
    return limits


def starter_slots_for(league_format: LeagueFormat) -> Dict[str, int]:
    """The starter slots the need-scoring cares about, in mock_draft's own
    position vocabulary (DST rather than DEF, SUPERFLEX kept separate)."""
    slots = {
        'QB': league_format.slot_count('QB'),
        'RB': league_format.slot_count('RB'),
        'WR': league_format.slot_count('WR'),
        'TE': league_format.slot_count('TE'),
        'SUPERFLEX': league_format.slot_count('SUPERFLEX'),
        'DST': league_format.slot_count('DEF'),
    }
    return {pos: count for pos, count in slots.items() if count}


def current_teams_from_keeper_board(per_team: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """Build {team_name: {manager, keeper1, keeper2}} from live keeper-board
    state (keeper_service.keeper_board_state()['per_team']) -- so the mock
    draft reflects whatever is actually selected on the keeper board right
    now. This is the only source of teams/keepers for the simulator; the old
    keeper_predictions_2026.csv path was removed 2026-08-11 once the
    interactive board superseded it (the CSV was a July snapshot that had
    already drifted out of sync with real selections).

    keeper1/keeper2 only (mock_draft's keeper slots are 2/team); manager is
    left blank since nothing downstream reads it. A team with 0 or 1 chosen
    keepers just gets fewer keeper slots filled -- simulate_draft() already
    tolerates empty keeper names."""
    teams = {}
    for entry in per_team:
        team_name = entry.get('team', '')
        if not team_name:
            continue
        chosen = entry.get('chosen', [])
        keeper_names = [c.get('playerName', '') for c in chosen[:2]]
        while len(keeper_names) < 2:
            keeper_names.append('')
        teams[team_name] = {
            'manager': '',
            'keeper1': keeper_names[0],
            'keeper2': keeper_names[1],
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


def _normalized_team_order(standings: List[Dict[str, Any]], current_team_names: set) -> List[str]:
    """Worst-record-first team order from a standings list, with team names
    normalized to their current display names.

    Team display names change year to year while the manager slots don't (see
    teamNames2025Note in league_rules.json), so a standings entry can carry a
    "now displayed as 'X'" note pointing at the current name."""
    standings = sorted(standings, key=lambda x: x.get('rank', 0))

    def normalize(entry: Dict[str, Any]) -> str:
        team = entry.get('team', '')
        if team in current_team_names:
            return team
        match = re.search(r"displayed as '([^']+)'", entry.get('note', '') or '')
        if match and match.group(1) in current_team_names:
            return match.group(1)
        return team

    return [normalize(entry) for entry in reversed(standings)]


def build_draft_order(
    repo: LeagueDataRepository,
    league_format: LeagueFormat,
    current_teams: Optional[Dict] = None,
    standings_year: Optional[int] = None,
) -> List[str]:
    """Snake draft order (one entry per pick) for this league's next draft.

    Worst record picks first, snaking each round, for league_format's own team
    and round counts. Honors traded-pick ownership when the league has a
    draft_picks file for the upcoming season; falls back to a plain snake when
    it doesn't (which is every non-Yahoo platform -- see repository.py).

    standings_year defaults to the league's most recent saved standings; the
    draft being ordered is the following season's."""
    if standings_year is None:
        available = repo.standings_years()
        if not available:
            raise FileNotFoundError(
                f"No saved standings for league '{repo.league.league_id}' -- can't derive a draft order.")
        standings_year = available[0]

    standings = repo.standings(standings_year)
    if not standings:
        raise FileNotFoundError(f"No standings for {standings_year} in league '{repo.league.league_id}'.")

    current_team_names = set(current_teams.keys()) if current_teams else set()
    team_order = _normalized_team_order(standings, current_team_names)

    # Traded-pick ownership for the draft that follows those standings.
    # repo.draft_picks() is already normalized to {teamName: {round: count}}
    # (see draft_picks.load_draft_picks); platforms that don't track traded
    # picks return {} and every team just picks once per round.
    team_picks = repo.draft_picks(standings_year + 1) or {}

    draft_order = []
    for round_num in range(1, league_format.total_draft_rounds + 1):
        round_team_order = team_order if round_num % 2 == 1 else list(reversed(team_order))
        for team in round_team_order:
            # No traded-pick data: everyone picks exactly once per round.
            num_picks = team_picks.get(team, {}).get(round_num, 1) if team_picks else 1
            for _ in range(num_picks):
                draft_order.append(team)

    return draft_order


def build_manager_profiles(
    rankings_lookup: Dict[str, Dict], repo: Optional[LeagueDataRepository] = None,
) -> Dict[str, Dict[str, Dict[int, float]]]:
    """Build manager personality profiles from this league's own draft history.

    Returns {team_name: {position: {round: frequency}}} -- how often each team
    has taken each position in each round. Uses every season the league has
    draft data for (was hardcoded to 2022-2025).

    Profiles are keyed by whatever team name that season's draft used; names
    drift year to year, so a team that got renamed simply has a thinner
    profile rather than a wrong one."""
    profiles = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    repo = repo if repo is not None else get_repository()

    for picks in repo.draft_years().values():
        for pick in picks:
            team = pick.get('team', '')
            if not team:
                continue
            player_data = rankings_lookup.get(str(pick.get('playerName', '')).lower().strip())
            if not player_data:
                continue
            pos = player_data.get('position', 'UNK')
            if pos != 'UNK':
                profiles[team][pos][pick.get('round', 0)] += 1

    # Normalize to frequencies
    for team in profiles:
        for pos in profiles[team]:
            total = sum(profiles[team][pos].values())
            if total > 0:
                for round_num in profiles[team][pos]:
                    profiles[team][pos][round_num] /= total

    return dict(profiles)


def build_position_need_scores(keeper_names: List[str], rankings_lookup: Dict,
                                 taken_this_round: List[str],
                                 starter_slots: Optional[Dict[str, int]] = None) -> Dict[str, float]:
    """Score position needs: higher score = more need. Used as tiebreak.

    starter_slots defaults to the default league's starter shape; pass
    starter_slots_for(league_format) for any other league."""
    if starter_slots is None:
        starter_slots = starter_slots_for(load_league_format())

    keeper_positions = defaultdict(int)

    for keeper_name in keeper_names:
        key = keeper_name.lower().strip()
        if key in rankings_lookup:
            pos = rankings_lookup[key].get('position', 'UNK')
            keeper_positions[pos] += 1

    # Count current coverage
    position_needs = {}
    for pos, slot_count in starter_slots.items():
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
    position_limits: Dict[str, int],
    late_round_start: int,
) -> float:
    """Score a player for this team/round. Higher = better pick.

    position_limits: from position_limits_for(league_format) -- how deep this
    league's roster shape wants to go at each position.
    late_round_start: first round considered 'late' for the defense boost,
    scaled to the league's draft length rather than a fixed round 12."""

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

    # Tiebreak 4: Elite defenses in the late rounds
    def_boost = 0
    if round_num >= late_round_start and pos == 'DST':
        nfl_team = player.get('team', '')
        if nfl_team in TOP_DEFENSES:
            def_boost = 8.0

    # Position limit penalties: penalize positions team has maxed out.
    # This makes maxed-out positions unattractive, so BPA shifts to available positions.
    # E.g., if team at 3 QBs, a rank-10 QB gets -80 penalty → rank-20 RB looks better.
    position_penalty = 0
    pos_limit = position_limits.get(pos, 999)
    pos_count = position_counts.get(pos, 0)

    if pos_limit == 0:
        # League has no slot for this position at all (e.g. K in a league that
        # dropped kickers) -- never draft it.
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
    league_format: Optional[LeagueFormat] = None,
) -> List[Dict[str, Any]]:
    """Simulate this league's draft. Returns list of picks.

    Round/team counts and which rounds are keeper slots come from
    league_format (defaults to the default league's). The simulation runs for
    as many picks as draft_order actually contains, so a league with traded
    picks -- where a round isn't exactly one pick per team -- still lines up."""
    league_format = league_format if league_format is not None else load_league_format()
    teams = league_format.teams
    keeper_rounds = league_format.keeper_slot_round_set
    position_limits = position_limits_for(league_format)
    starter_slots = starter_slots_for(league_format)
    # "Late" defense-boost rounds: the final fifth of the draft, i.e. round 12
    # of 15 -- the value this was hardcoded to before the per-league port.
    late_round_start = max(1, (league_format.total_draft_rounds * 4) // 5)

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

    # One iteration per entry in draft_order: teams x rounds normally, but with
    # traded picks a round isn't evenly one-per-team, so the order list is the
    # authority on how many picks there are, not teams * rounds.
    for pick_num, team in enumerate(draft_order, start=1):
        round_num = (pick_num - 1) // teams + 1

        # Check if this is a keeper round for this team
        is_keeper_round = round_num in keeper_rounds
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
                'pickInRound': (pick_num - 1) % teams + 1,
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
        position_needs = build_position_need_scores(
            keeper_predictions.get(team, []), rankings_lookup, taken_by_team[team], starter_slots)

        # Find best available player. Floor is -inf, not -1: a team that has
        # maxed out every position it wants still has to use the pick, and
        # every candidate can legitimately score negative once position
        # penalties apply (-1000 for a position the league has no slot for).
        # A -1 floor silently produced no pick at all for those slots.
        best_player = None
        best_score = float('-inf')

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
                position_limits=position_limits,
                late_round_start=late_round_start,
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
                'pickInRound': (pick_num - 1) % teams + 1,
                'team': team,
                'playerName': best_player.get('playerName', ''),
                'position': pos,
                'rank': best_player.get('adjustedRank', best_player.get('ranking', 0)),
                'nflTeam': best_player.get('team', ''),
                'isKeeper': False,
            })

    return mock_picks


def run_mock_draft(
    current_teams: Dict[str, Dict[str, str]],
    repo: Optional[LeagueDataRepository] = None,
    league_format: Optional[LeagueFormat] = None,
) -> List[Dict[str, Any]]:
    """End-to-end: load data, simulate draft, return picks.

    current_teams: {team_name: {manager, keeper1, keeper2}}, built by
    current_teams_from_keeper_board() from live keeper-board state. Required
    -- the old keeper_predictions_2026.csv default was removed 2026-08-11
    because it silently simulated a stale July snapshot instead of the
    league's real current selections.

    repo/league_format default to the default league; pass both to simulate
    any other registered league."""
    repo = repo if repo is not None else get_repository()
    league_format = league_format if league_format is not None else load_league_format()

    keeper_predictions = {team: [v['keeper1'], v['keeper2']] for team, v in current_teams.items()}
    rankings_lookup, rankings_all = load_adjusted_rankings()
    draft_order = build_draft_order(repo, league_format, current_teams=current_teams)
    manager_profiles = build_manager_profiles(rankings_lookup, repo=repo)

    return simulate_draft(
        keeper_predictions, rankings_lookup, rankings_all, draft_order, manager_profiles,
        league_format=league_format,
    )


def export_mock_draft(
    picks: List[Dict[str, Any]], output_dir: Optional[Path] = None, filename: str = 'mock_draft.csv',
) -> Path:
    """Export mock draft to CSV."""
    if output_dir is None:
        output_dir = PROCESSED_DIR

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / filename

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
