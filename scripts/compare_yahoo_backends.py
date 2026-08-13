#!/usr/bin/env python3
"""Assert YahooDbRepository returns exactly what YahooJsonRepository returned.

The gate on the JSON->DB migration. CLAUDE.md records two prior bugs from
this same class of storage swap (a position-resolution name collision, and
five silent wrong-output bugs in the mock draft port) -- both silent, both
caught only by diffing full output rather than checking "did it run". This
does that diff for every method, every year, not a spot check.

Run after `python3 -m app migrate-yahoo-data`:

    python3 scripts/compare_yahoo_backends.py

Exits non-zero on any difference. Point DATABASE_URL at Railway's Postgres
to verify the production load the same way (the JSON side still reads the
local files, which is the point -- they are the reference).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
from app.league_registry import get_league
from app.repository import YahooDbRepository, YahooJsonRepository


def _describe(value) -> str:
    text = repr(value)
    return text if len(text) <= 240 else text[:240] + f'... ({len(text)} chars)'


def _compare(label, expected, actual, failures) -> None:
    if expected == actual:
        print(f'  ok   {label}')
        return
    failures.append(label)
    print(f'  FAIL {label}')
    print(f'       json: {_describe(expected)}')
    print(f'       db:   {_describe(actual)}')
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) != len(actual):
        print(f'       (length {len(expected)} vs {len(actual)})')


def main() -> int:
    league = get_league('frank-gore')
    json_repo = YahooJsonRepository(league)
    db_repo = YahooDbRepository(league)
    failures = []

    print('rosters()')
    _compare('rosters()', json_repo.rosters(), db_repo.rosters(), failures)

    print('draft_years()')
    json_years = json_repo.draft_years()
    db_years = db_repo.draft_years()
    _compare('draft_years() keys', sorted(json_years), sorted(db_years), failures)
    for year in sorted(json_years):
        _compare(f'draft_years()[{year}]', json_years.get(year), db_years.get(year), failures)

    print('standings_years()')
    json_standings_years = json_repo.standings_years()
    _compare('standings_years()', json_standings_years, db_repo.standings_years(), failures)

    print('standings(year)')
    for year in json_standings_years:
        _compare(f'standings({year})', json_repo.standings(year), db_repo.standings(year), failures)

    print('draft_picks(year) / draft_pick_origins(year)')
    # Every year either repo might know about, plus the upcoming draft year
    # that has ownership but no picks yet.
    candidate_years = sorted(set(json_years) | set(json_standings_years)
                             | {max(json_years or [0]) + 1, max(json_standings_years or [0]) + 1})
    for year in candidate_years:
        _compare(f'draft_picks({year})', json_repo.draft_picks(year), db_repo.draft_picks(year), failures)
        _compare(f'draft_pick_origins({year})',
                 json_repo.draft_pick_origins(year), db_repo.draft_pick_origins(year), failures)

    print()
    if failures:
        print(f'{len(failures)} difference(s): ' + ', '.join(failures))
        return 1
    print('All backends agree.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
