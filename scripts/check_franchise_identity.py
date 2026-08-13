#!/usr/bin/env python3
"""Assert franchise identity survives a team rename (Phase 5 step 2 gate).

The whole point of `franchises` is that a manager renaming their team must
not orphan their keeper marks. That is impossible to verify by reading the
code -- the failure mode is silent, an unmatched mark simply stops applying
-- so this exercises it end to end against a throwaway SQLite database:
write marks under one team name, rename the team the way each platform
renames it, and assert the marks come back under the new name.

    python3 scripts/check_franchise_identity.py

Exits non-zero on the first failed assertion.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ['WUFF_DISABLE_SCHEDULER'] = '1'
_DB = Path(tempfile.mkdtemp(prefix='wuff-franchise-')) / 'check.db'
os.environ['DATABASE_URL'] = f'sqlite:///{_DB}'

# pylint: disable=wrong-import-position
from app.db import SessionLocal, init_db  # noqa: E402
from app.franchise_registry import Franchise, FranchiseRegistry  # noqa: E402
from app.franchise_store import (  # noqa: E402
    backfill_keeper_marks,
    get_registry,
    rebuild_league,
    reset_cache,
)
from app.keeper_service import load_keeper_marks  # noqa: E402
from app.league_context import LeagueFormat  # noqa: E402
from app.league_registry import League  # noqa: E402
from app.models import KeeperMark  # noqa: E402

PASSES = []
FAILURES = []


def check(label: str, condition: bool, detail: str = '') -> None:
    (PASSES if condition else FAILURES).append(label)
    status = 'ok  ' if condition else 'FAIL'
    print(f'  {status} {label}{"" if condition else f"  <- {detail}"}')


class FakeRepo:
    """Minimal LeagueDataRepository stand-in: rosters + standings only, which
    is all franchise identity reads."""

    def __init__(self, rosters, standings_by_year=None):
        self._rosters = rosters
        self._standings = standings_by_year or {}

    def rosters(self):
        return self._rosters

    def standings_years(self):
        return sorted(self._standings, reverse=True)

    def standings(self, year):
        return self._standings.get(year)


def league(platform, platform_league_id):
    return League(league_id=f'{platform}-{platform_league_id}', platform=platform,
                  platform_league_id=platform_league_id, name='Check league',
                  format=LeagueFormat())


def add_mark(lg, team, player, franchise_id):
    with SessionLocal() as session:
        session.add(KeeperMark(platform=lg.platform, platform_league_id=lg.platform_league_id,
                               team_name=team, franchise_id=franchise_id,
                               player_name=player, action='include'))
        session.commit()


def marks_for(lg, repo):
    reset_cache()
    include, _ = load_keeper_marks(lg.platform, lg.platform_league_id,
                                   franchises=get_registry(lg, repo))
    return include


def check_snapshot_rename() -> None:
    """Sleeper/ESPN: teamName changes, ownerId does not."""
    print('\nSleeper-shaped league (identity from ownerId):')
    lg = league('sleeper', '900001')
    before = FakeRepo([{'rosterId': 1, 'ownerId': 'owner-A', 'teamName': 'Old Name'}])
    rebuild_league(lg, before)
    reset_cache()

    franchise_id = get_registry(lg, before).id_for_name('Old Name')
    check('franchise resolves from ownerId', franchise_id == 'sleeper:900001:owner:owner-A', franchise_id)
    add_mark(lg, 'Old Name', 'Bijan Robinson', franchise_id)
    check('mark applies before rename', marks_for(lg, before).get('Old Name') == ['Bijan Robinson'],
          str(marks_for(lg, before)))

    after = FakeRepo([{'rosterId': 1, 'ownerId': 'owner-A', 'teamName': 'Brand New Name'}])
    rebuild_league(lg, after)
    reset_cache()
    marks = marks_for(lg, after)
    check('mark FOLLOWS the rename', marks.get('Brand New Name') == ['Bijan Robinson'], str(marks))
    check('mark is not left under the old name', 'Old Name' not in marks, str(marks))


def check_alias_file_rename() -> None:
    """Yahoo: no platform id, so the hand-authored alias file carries identity."""
    print('\nYahoo-shaped league (identity from the alias file):')
    lg = league('yahoo', '900002')
    aliases = {'yahoo': {'900002': {'the-crew': ['Old Crew', 'New Crew']}}}
    repo = FakeRepo(
        [{'teamName': 'New Crew'}],
        {2025: [{'team': 'New Crew', 'rank': 1}], 2024: [{'team': 'Old Crew', 'rank': 4}]},
    )
    from app.franchise_registry import build_franchises  # pylint: disable=import-outside-toplevel
    from app.franchise_store import save_franchises  # pylint: disable=import-outside-toplevel
    save_franchises('yahoo', '900002', build_franchises(repo, lg, aliases=aliases))
    reset_cache()

    registry = get_registry(lg, repo)
    old_id = registry.id_for_name('Old Crew')
    new_id = registry.id_for_name('New Crew')
    check('both names resolve to ONE franchise', old_id is not None and old_id == new_id,
          f'{old_id} vs {new_id}')

    add_mark(lg, 'Old Crew', 'Puka Nacua', old_id)
    marks = marks_for(lg, repo)
    check('mark made under the old name shows under the new one',
          marks.get('New Crew') == ['Puka Nacua'], str(marks))


def check_unresolved_fallback() -> None:
    """A franchise nothing can identify must behave exactly as it did before
    this table existed -- name matching, no crash, no silent drop."""
    print('\nUnresolved franchise (must degrade to the old behaviour):')
    lg = league('yahoo', '900003')
    repo = FakeRepo([{'teamName': 'Mystery Team'}], {})
    rebuild_league(lg, repo)
    reset_cache()

    add_mark(lg, 'Some Other Team', 'Jahmyr Gibbs', None)
    marks = marks_for(lg, repo)
    check('NULL franchise_id still matches on team_name',
          marks.get('Some Other Team') == ['Jahmyr Gibbs'], str(marks))

    stats = backfill_keeper_marks(lg, repo)
    check('backfill reports the unresolved row rather than guessing',
          stats['unresolved'] == 1 and stats['stamped'] == 0, str(stats))


def check_registry_lookups() -> None:
    print('\nRegistry lookups:')
    registry = FranchiseRegistry([
        Franchise(franchise_id='f1', platform='sleeper', platform_league_id='1', name='Now',
                  owner_id='o1', roster_id='3', names=['Now', 'Then']),
    ])
    check('by_name finds an older name', registry.by_name('Then').franchise_id == 'f1')
    check('by_owner_id', registry.by_owner_id('o1').franchise_id == 'f1')
    check('by_roster_id', registry.by_roster_id(3).franchise_id == 'f1')
    check('unknown name returns None', registry.by_name('Nobody') is None)


def main() -> int:
    init_db()
    check_snapshot_rename()
    check_alias_file_rename()
    check_unresolved_fallback()
    check_registry_lookups()

    print()
    if FAILURES:
        print(f'{len(FAILURES)} check(s) FAILED: {", ".join(FAILURES)}')
        return 1
    print(f'Franchise identity holds ({len(PASSES)} checks).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
