"""QB historical-draft-slot adjustment.

Standard draft-forecasting method (2026+): start from a straight PPR ranking file
(not a superflex-inflated one), then nudge the top N QBs up to the overall pick
where a QB of that rank has actually gone in this league's own draft history,
instead of hand-tuning QB shifts by feel. Replaced the old
board_adjustments.json / rankings_adjusted.json QB-knockback approach for the
post-keeper draft board and keeper selection; that path (app/ranking_adjustments.py
and the adjust-rankings command) was deleted 2026-08-11.

Targets are recomputed from data/raw/draft_history/{year}.json each run (via
live_draft_picks, which already excludes the last-2-rounds keeper slots so kept
QBs don't skew "real" draft demand) rather than hardcoded, so they stay current
as more draft years are added.
"""
from statistics import mean
from typing import Any, Dict, List, Optional

from .draft_history import load_draft_years, live_draft_picks
from .nfl_stats import fantasy_position_map

DEFAULT_TEAMS = 12
DEFAULT_TOP_N = 7


def compute_historical_qb_pick_targets(
    years: Optional[List[int]] = None,
    teams: int = DEFAULT_TEAMS,
    years_data: Optional[Dict[int, List[dict]]] = None,
) -> List[int]:
    """Average overall pick number for QB1, QB2, ... QBn across past drafts.

    Only years with nflverse roster data available (for position lookup) are
    usable; years without it are silently skipped. Keeper-slot rounds are
    excluded via live_draft_picks so a QB's keeper round doesn't get counted
    as fresh draft-day demand.

    Pass years_data (from a repository's draft_years()) rather than letting
    it read the JSON files: draft history lives in the database now, and the
    JSON fallback returns {} on a deployed container, which would silently
    drop the adjustment instead of failing.
    """
    years_data = years_data if years_data is not None else load_draft_years()
    candidate_years = years if years is not None else sorted(years_data.keys())

    picks_by_qb_rank: Dict[int, List[int]] = {}

    for year in candidate_years:
        # Must be fantasy_position_map, not a plain dict over load_rosters():
        # Josh Allen and Lamar Jackson each share a name with a defender, and a
        # naive map labeled them DB/LB -- silently excluding this league's
        # round-1 rushing QBs from its own QB draft-slot targets.
        position_map = fantasy_position_map(year)
        if not position_map:
            continue

        qb_picks = []
        for pick in live_draft_picks(year, years_data):
            player_name = str(pick.get('playerName', '')).strip().lower()
            if position_map.get(player_name) != 'QB':
                continue
            overall_pick = (pick.get('round', 0) - 1) * teams + pick.get('pick', 0)
            qb_picks.append(overall_pick)

        qb_picks.sort()
        for qb_rank, overall_pick in enumerate(qb_picks, start=1):
            picks_by_qb_rank.setdefault(qb_rank, []).append(overall_pick)

    if not picks_by_qb_rank:
        return []

    max_rank = max(picks_by_qb_rank.keys())
    return [
        round(mean(picks_by_qb_rank[qb_rank])) if qb_rank in picks_by_qb_rank else None
        for qb_rank in range(1, max_rank + 1)
    ]


def apply_qb_historical_adjustment(
    rankings: List[Dict[str, Any]],
    top_n: int = DEFAULT_TOP_N,
    targets: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """Move the top N QBs (by current 'ranking') to their historical target pick,
    then renumber the whole list 1..N so ranks stay sequential and unique.

    On a tie between a shifted QB and an unshifted player, the QB wins the slot
    (matches "Josh Allen becomes #1 overall" style expectations from a flat shift).
    Returns a new list; does not mutate the input.
    """
    if targets is None:
        targets = compute_historical_qb_pick_targets()
    if not targets:
        raise ValueError('No historical QB pick targets available (no draft history with roster data).')

    qbs_sorted = sorted(
        (entry for entry in rankings if entry.get('position') == 'QB'),
        key=lambda e: e.get('ranking', 9999),
    )
    top_qbs = qbs_sorted[:top_n]

    target_rank_by_name = {}
    for i, entry in enumerate(top_qbs):
        target = targets[i] if i < len(targets) else targets[-1]
        target_rank_by_name[entry['playerName']] = target

    annotated = []
    for entry in rankings:
        original_rank = entry.get('ranking', 9999)
        is_target = entry.get('playerName') in target_rank_by_name
        effective_rank = target_rank_by_name[entry['playerName']] if is_target else original_rank
        annotated.append((effective_rank, 0 if is_target else 1, original_rank, dict(entry)))

    annotated.sort(key=lambda t: (t[0], t[1], t[2]))

    result = []
    for i, (_, _, _, entry) in enumerate(annotated, start=1):
        entry['ranking'] = i
        result.append(entry)

    return result
