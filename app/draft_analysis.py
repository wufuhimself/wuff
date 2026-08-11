"""Historical draft-outcome analysis: does where you picked predict where you finished?

Per-league since 2026-08-11 (Phase 3 port, docs/roadmap.md). Every entry point
takes an optional `repo` (app/repository.py LeagueDataRepository) and reads that
league's own draft history + standings through it. Omit `repo` and it falls back
to the default league's repository, which is what the original Yahoo-only
version did implicitly — so existing callers are unchanged.

Note both analyses correlate against *final standings rank*, so they only
produce output for seasons where the league has both draft data and saved
standings. A freshly imported league with one synced season has nothing to
correlate yet; that's an empty result, not an error.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from statistics import mean, median

from .draft_history import live_draft_picks
from .repository import LeagueDataRepository, get_repository
from .standings import current_team_names as get_current_team_names
from .nfl_stats import fantasy_position_map


@dataclass
class DraftSlotOutcome:
    year: int
    team: str
    draft_slot: int
    final_rank: int


@dataclass
class RoundPositionOutcome:
    year: int
    team: str
    round: int
    position: str
    final_rank: int


def draft_slot_vs_final_rank(
    years: Optional[List[int]] = None, repo: Optional[LeagueDataRepository] = None,
) -> List[DraftSlotOutcome]:
    """Pair each team's round-1 draft slot with their final rank that season.
    If years=None, use all years this league has draft data for.
    repo defaults to the default league's repository."""
    repo = repo if repo is not None else get_repository()
    draft_years = repo.draft_years()
    if years is None:
        years = sorted(draft_years.keys())

    outcomes = []

    for year in years:
        standings = repo.standings(year)
        if standings is None:
            continue

        picks = live_draft_picks(year, draft_years)
        if not picks:
            continue

        # Resolve within-year renames via 'note' field
        aliases = get_current_team_names(standings)

        # Build standings map: team display name -> rank
        standings_by_team = {}
        for entry in standings:
            team = entry.get('team')
            rank = entry.get('rank')
            if team and rank:
                # Use alias if available, else use team as-is
                display_name = aliases.get(team, team)
                standings_by_team[team] = rank
                standings_by_team[display_name] = rank

        # Find round 1 picks
        round1_picks = [p for p in picks if p.get('round') == 1]
        for idx, pick in enumerate(sorted(round1_picks, key=lambda p: p.get('pick', 999)), start=1):
            team_name = pick.get('team')
            if not team_name:
                continue

            final_rank = standings_by_team.get(team_name)
            if final_rank is not None:
                outcomes.append(DraftSlotOutcome(
                    year=year,
                    team=team_name,
                    draft_slot=idx,
                    final_rank=int(final_rank)
                ))

    return outcomes


def summarize_draft_slot_correlation(outcomes: List[DraftSlotOutcome]) -> Dict[str, Any]:
    """Compute slot -> final_rank correlation and per-slot averages.
    Returns: {slot_to_avg_rank, correlation, n_samples, caveat}"""
    if not outcomes:
        return {'error': 'No outcomes to analyze'}

    # Group by slot
    slot_to_ranks = {}
    for outcome in outcomes:
        slot = outcome.draft_slot
        if slot not in slot_to_ranks:
            slot_to_ranks[slot] = []
        slot_to_ranks[slot].append(outcome.final_rank)

    # Compute averages
    slot_to_avg = {}
    for slot in sorted(slot_to_ranks.keys()):
        ranks = slot_to_ranks[slot]
        slot_to_avg[slot] = {
            'avg_rank': round(mean(ranks), 1),
            'median_rank': median(ranks),
            'n': len(ranks),
            'samples': ranks,
        }

    # Pearson correlation (manual): slot vs rank
    if len(outcomes) > 1:
        slots = [o.draft_slot for o in outcomes]
        ranks = [o.final_rank for o in outcomes]
        mean_slot = mean(slots)
        mean_rank = mean(ranks)
        cov = sum((s - mean_slot) * (r - mean_rank) for s, r in zip(slots, ranks)) / len(outcomes)
        std_slot = (sum((s - mean_slot) ** 2 for s in slots) / len(outcomes)) ** 0.5
        std_rank = (sum((r - mean_rank) ** 2 for r in ranks) / len(outcomes)) ** 0.5
        correlation = cov / (std_slot * std_rank) if std_slot * std_rank > 0 else 0.0
    else:
        correlation = 0.0

    years_covered = len({o.year for o in outcomes})
    return {
        'slot_to_avg': slot_to_avg,
        'correlation': round(correlation, 3),
        'n_samples': len(outcomes),
        'years_covered': years_covered,
        'caveat': (
            f'Small sample: {len(outcomes)} team-seasons across {years_covered} '
            'season(s) with both draft and standings data. Treat as directional, '
            'not conclusive.'
        ),
    }


def position_in_round_vs_final_rank(
    round_number: int, years: Optional[List[int]] = None, repo: Optional[LeagueDataRepository] = None,
) -> List[RoundPositionOutcome]:
    """For a given round, pair position drafted with final team rank that season.
    Requires nflverse rosters data (app/nfl_stats.py) for position lookups --
    those are real-NFL data, shared across leagues, not per-league.
    repo defaults to the default league's repository."""
    repo = repo if repo is not None else get_repository()
    draft_years = repo.draft_years()
    if years is None:
        years = sorted(draft_years.keys())

    outcomes = []

    # Pre-load position maps. fantasy_position_map (not a raw roster dict)
    # breaks name collisions with defensive namesakes -- otherwise Josh Allen
    # and Lamar Jackson show up here as phantom 'LB'/'DB' round-1 picks.
    rosters_by_year = {}
    for year in years:
        position_map = fantasy_position_map(year)
        if position_map:
            rosters_by_year[year] = position_map

    for year in years:
        standings = repo.standings(year)
        if standings is None:
            continue

        picks = live_draft_picks(year, draft_years)
        if not picks:
            continue

        position_map = rosters_by_year.get(year)
        if not position_map:
            # No nflverse roster snapshot for this season -- can't resolve
            # positions, so skip the year. Silent: this is also called from a
            # web route, where printing to stdout is noise.
            continue

        # Resolve within-year renames via 'note' field
        aliases = get_current_team_names(standings)

        # Build standings map: team -> rank (both original and alias names)
        standings_by_team = {}
        for entry in standings:
            team = entry.get('team')
            rank = entry.get('rank')
            if team and rank:
                display_name = aliases.get(team, team)
                standings_by_team[team] = rank
                standings_by_team[display_name] = rank

        # Find picks in this round
        round_picks = [p for p in picks if p.get('round') == round_number]
        for pick in round_picks:
            team_name = pick.get('team')
            player_name = pick.get('playerName', '')
            if not team_name or not player_name:
                continue

            final_rank = standings_by_team.get(team_name)
            if final_rank is None:
                continue

            # Lookup position
            position = position_map.get(player_name.lower(), 'UNKNOWN')

            outcomes.append(RoundPositionOutcome(
                year=year,
                team=team_name,
                round=round_number,
                position=position,
                final_rank=int(final_rank)
            ))

    return outcomes


def summarize_position_in_round(outcomes: List[RoundPositionOutcome]) -> Dict[str, Any]:
    """Group outcomes by position and compute stats.
    Returns: {position: {avg_rank, median_rank, n, ...}}"""
    if not outcomes:
        return {'error': 'No outcomes to analyze'}

    position_to_ranks = {}
    for outcome in outcomes:
        pos = outcome.position
        if pos not in position_to_ranks:
            position_to_ranks[pos] = []
        position_to_ranks[pos].append(outcome.final_rank)

    result = {}
    for pos in sorted(position_to_ranks.keys()):
        ranks = position_to_ranks[pos]
        result[pos] = {
            'avg_rank': round(mean(ranks), 1),
            'median_rank': median(ranks),
            'n': len(ranks),
        }

    return result
