"""What this league's managers actually draft, and when.

Answers "what goes at round N here" from the league's own draft history rather
than from a national board's rank or a hand-picked constant. Generalizes the
QB-only logic in qb_historical_adjustment.py to every position.

Per-league: every entry point takes a repository (app/repository.py) and reads
that league's own drafts; omit it for the default league.

Data reality worth knowing before trusting any of this:
- Draft-history picks carry no position, so it's resolved via
  nfl_stats.load_rosters(year). That limits usable seasons to ones with an
  nflverse roster snapshot saved (2022+ locally) and resolves ~82-91% of picks.
- Roughly 4 seasons x ~140 resolved picks. That supports **per-round**
  aggregates (~45 samples/round); it does NOT support per-exact-pick ones
  (~3 samples/slot). Don't build pick-level models on this without more years.
"""
from statistics import mean, median
from typing import Any, Dict, List, Optional

from .draft_history import live_draft_picks
from .nfl_stats import fantasy_position_map
from .repository import LeagueDataRepository, get_repository

# Positions worth reporting on; anything else resolves to OTHER.
TRACKED_POSITIONS = ('QB', 'RB', 'WR', 'TE', 'DST', 'K')
_DEF_ALIASES = {'DEF', 'DST', 'D/ST'}


def _normalize_position(value: Any) -> Optional[str]:
    text = str(value or '').strip().upper()
    if not text:
        return None
    if text in _DEF_ALIASES:
        return 'DST'
    return text if text in TRACKED_POSITIONS else 'OTHER'


def resolved_picks(
    repo: Optional[LeagueDataRepository] = None, years: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """Live draft picks (keeper slots excluded) with a resolved position.

    Each entry: {year, round, pick, overall, position, playerName, team}.
    Picks whose player can't be matched to a roster snapshot are dropped --
    that's the ~10-18% resolution gap noted in the module docstring."""
    repo = repo if repo is not None else get_repository()
    draft_years = repo.draft_years()
    teams = repo.league.format.teams
    candidate_years = years if years is not None else sorted(draft_years.keys())

    out: List[Dict[str, Any]] = []
    for year in candidate_years:
        position_map = fantasy_position_map(year)
        if not position_map:
            continue
        for pick in live_draft_picks(year, draft_years):
            name = str(pick.get('playerName', '')).strip().lower()
            position = _normalize_position(position_map.get(name))
            if position is None:
                continue
            round_num = pick.get('round') or 0
            in_round = pick.get('pick') or 0
            out.append({
                'year': year,
                'round': round_num,
                'pick': in_round,
                'overall': (round_num - 1) * teams + in_round,
                'position': position,
                'playerName': pick.get('playerName'),
                'team': pick.get('team'),
            })
    return out


def position_mix_by_round(
    repo: Optional[LeagueDataRepository] = None, years: Optional[List[int]] = None,
) -> Dict[int, Dict[str, Any]]:
    """{round: {'n': picks_sampled, 'mix': {position: fraction_of_that_round}}}.

    This is the direct answer to "what do managers take in round N here."
    """
    picks = resolved_picks(repo, years)
    by_round: Dict[int, List[str]] = {}
    for pick in picks:
        by_round.setdefault(pick['round'], []).append(pick['position'])

    result = {}
    for round_num, positions in sorted(by_round.items()):
        total = len(positions)
        counts: Dict[str, int] = {}
        for position in positions:
            counts[position] = counts.get(position, 0) + 1
        result[round_num] = {
            'n': total,
            'mix': {pos: round(count / total, 3) for pos, count in
                    sorted(counts.items(), key=lambda kv: -kv[1])},
        }
    return result


def position_timing(
    repo: Optional[LeagueDataRepository] = None, years: Optional[List[int]] = None,
) -> Dict[str, Dict[str, Any]]:
    """When each position actually comes off the board.

    {position: {n, first_round, median_round, mean_round, last_round,
                rounds_seen}} -- 'first_round' is the earliest round that
    position was EVER taken across the sampled drafts, which is the honest
    input for a "don't draft this before round N" floor."""
    picks = resolved_picks(repo, years)
    by_position: Dict[str, List[int]] = {}
    for pick in picks:
        by_position.setdefault(pick['position'], []).append(pick['round'])

    result = {}
    for position, rounds in by_position.items():
        result[position] = {
            'n': len(rounds),
            'first_round': min(rounds),
            'median_round': median(rounds),
            'mean_round': round(mean(rounds), 1),
            'last_round': max(rounds),
            'rounds_seen': sorted(set(rounds)),
        }
    return dict(sorted(result.items(), key=lambda kv: kv[1]['mean_round']))


def position_rank_pick_targets(
    position: str, top_n: int = 12,
    repo: Optional[LeagueDataRepository] = None, years: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """Average overall pick for the Nth player at a position (QB1, QB2, ...).

    Generalizes compute_historical_qb_pick_targets() in
    qb_historical_adjustment.py to any position. Rank is by draft order within
    each season, so 'QB3' means the third QB off the board that year, not a
    preseason ranking."""
    picks = resolved_picks(repo, years)
    wanted = _normalize_position(position)

    by_year: Dict[int, List[int]] = {}
    for pick in sorted(picks, key=lambda p: (p['year'], p['overall'])):
        if pick['position'] == wanted:
            by_year.setdefault(pick['year'], []).append(pick['overall'])

    targets = []
    for rank in range(1, top_n + 1):
        overalls = [picks_in_year[rank - 1] for picks_in_year in by_year.values()
                    if len(picks_in_year) >= rank]
        if not overalls:
            break
        targets.append({
            'positionRank': f'{wanted}{rank}',
            'avgOverallPick': round(mean(overalls), 1),
            'medianOverallPick': median(overalls),
            'n': len(overalls),
        })
    return targets
