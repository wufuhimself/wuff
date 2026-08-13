#!/usr/bin/env python3
"""Coverage report for the cross-platform player registry (Phase 5 step 1 gate).

Resolves every player name wuff actually handles -- every registered league's
rosters, every season of every league's draft history, and the rankings board
-- through app/player_registry.py, and reports how many resolve, how many are
ambiguous, and how many are unknown.

This deliberately does NOT assert zero unresolved names, and must not be
"fixed" until it does. Some names cannot resolve and never will:

- historical draft picks for players who have since left the league entirely
  and aged out of both source datasets;
- ranking-board rows for players Sleeper has not added yet.

Forcing the number to zero would mean guessing, and guessing is the exact bug
this registry exists to remove (see nfl_stats.fantasy_position_map: Josh
Allen the BUF QB vs Josh Allen the JAX LB). What this script is a gate on is
*regression* -- run it before and after any change to the registry or its
sources and compare the numbers.

    python3 scripts/check_player_resolution.py
    python3 scripts/check_player_resolution.py --show-unresolved 40

Exits non-zero only when a source resolves below its floor, i.e. when
something structural has broken rather than a few players having churned.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
from app.league_registry import load_leagues
from app.player_registry import RESOLVED, UNRESOLVED_AMBIGUOUS
from app.player_store import get_registry
from app.repository import repository_for

# Minimum share of names that must resolve per source before this is treated
# as a structural break rather than normal churn. Rosters are current players
# and should be near-total; draft history reaches back seasons and will always
# trail. Tighten these as coverage improves -- never loosen them to make a red
# run go green.
FLOORS = {
    # Measured at first build 2026-08-13: rosters 100.0%, rankings 99.8%,
    # draft_history 99.7%. Floors sit a few points under that -- enough slack
    # for normal roster churn, tight enough that a structural break (a source
    # format change, a normalization regression) fails loudly.
    'rosters': 0.98,
    'rankings': 0.97,
    'draft_history': 0.97,
}

Sample = Tuple[str, Optional[str], Optional[str]]  # (name, team, position)


def _collect(league_id: str) -> Dict[str, List[Sample]]:
    """Every (name, team, position) wuff would want to resolve for one league."""
    repo = repository_for(load_leagues()[league_id])
    samples: Dict[str, List[Sample]] = {'rosters': [], 'draft_history': [], 'rankings': []}

    for team in repo.rosters():
        for player in team.get('players') or []:
            name = player.get('playerName')
            if name:
                samples['rosters'].append((name, player.get('team'), player.get('position')))

    for picks in repo.draft_years().values():
        for pick in picks:
            name = pick.get('playerName')
            if name:
                # Draft history carries no position for the Yahoo league --
                # that is the case the registry has to survive, so it is passed
                # through as-is rather than back-filled here.
                samples['draft_history'].append((name, pick.get('nflTeam'), pick.get('position')))

    for row in repo.rankings():
        name = row.get('playerName')
        if name:
            samples['rankings'].append((name, row.get('team'), row.get('position')))

    return samples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--show-unresolved', type=int, default=15,
                        help='How many unresolved names to print per source (0 to hide)')
    parser.add_argument('--league', default=None, help='Limit to one league id')
    args = parser.parse_args()

    registry = get_registry()
    print(f'Registry: {len(registry)} players\n')
    if not len(registry):  # pylint: disable=len-as-condition
        print('Registry is empty. Run `python3 -m app build-player-registry` first.', file=sys.stderr)
        return 1

    league_ids = [args.league] if args.league else list(load_leagues())
    totals: Dict[str, Counter] = {source: Counter() for source in FLOORS}
    unresolved: Dict[str, Counter] = {source: Counter() for source in FLOORS}

    for league_id in league_ids:
        try:
            samples = _collect(league_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f'  {league_id}: skipped ({type(exc).__name__}: {exc})')
            continue
        line = [f'{league_id}:']
        for source, entries in samples.items():
            counts = Counter()
            for name, team, position in entries:
                _, reason = registry.resolve_detail(name, team=team, position=position)
                counts[reason] += 1
                if reason != RESOLVED:
                    unresolved[source][f'{name} [{position or "?"}/{team or "?"}] {reason}'] += 1
            totals[source].update(counts)
            total = sum(counts.values())
            if total:
                line.append(f'{source} {counts[RESOLVED]}/{total}')
        print('  ' + '  '.join(line))

    print()
    failed = False
    for source, floor in FLOORS.items():
        counts = totals[source]
        total = sum(counts.values())
        if not total:
            print(f'{source:14} no samples')
            continue
        rate = counts[RESOLVED] / total
        status = 'OK ' if rate >= floor else 'LOW'
        if rate < floor:
            failed = True
        print(f'{source:14} {status} {counts[RESOLVED]}/{total} resolved ({rate:.1%}, floor {floor:.0%})  '
              f'ambiguous={counts[UNRESOLVED_AMBIGUOUS]} unknown={counts["unknown"]}')

    if args.show_unresolved:
        for source in FLOORS:
            if not unresolved[source]:
                continue
            print(f'\nTop unresolved — {source}:')
            for label, count in unresolved[source].most_common(args.show_unresolved):
                print(f'  {count:4}x  {label}')

    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
