#!/usr/bin/env python3
"""Assert bye weeks and injury designations reach the overview page.

Phase 5 step 5 resolved RosterEntry.status/.injury_status/.bye_week off the
player registry, but nothing rendered them -- the data was correct and
invisible, which is indistinguishable from broken to a user. Three claims,
all silent if they break:

1. Byes resolve for every player who HAS an NFL team. A player without one is
   a free agent and correctly has no bye; a player WITH one and no bye is the
   bug this script was written after (a resolved identity carrying team=None
   was overriding the roster's own team, silently dropping the lookup).

2. Injury severity buckets correctly. 'Questionable' must not read the same as
   'IR' -- an out player and a maybe player are different decisions.

3. The overview page actually renders both. Matching the rendered ELEMENT
   (class="pill inj ...") not a bare class name, because base.html's inline
   <style> block carries every class on every page regardless.

    python3 scripts/check_roster_health.py

Exits non-zero on any failed expectation. Runs against the real Flask app and
a throwaway COPY of the local database, like check_keeper_board_focus.py.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_REAL_DB = Path(__file__).resolve().parent.parent / 'data' / 'wuff.db'
_TMP_DB = Path(tempfile.mkdtemp(prefix='wuff-rosterhealth-')) / 'health.db'
# A COPY of the real local database: the rosters this checks live in the
# database (Yahoo's since 2026-08-12, Sleeper's since 2026-08-19), so an empty
# schema has nothing to check. Still throwaway -- the test user goes in the
# copy and the real database is never opened for writing.
if _REAL_DB.exists():
    shutil.copyfile(_REAL_DB, _TMP_DB)
os.environ['DATABASE_URL'] = f'sqlite:///{_TMP_DB}'
os.environ['WUFF_DISABLE_SCHEDULER'] = '1'

# pylint: disable=wrong-import-position
from app.db import SessionLocal, init_db  # noqa: E402
from app.domain import RosterEntry  # noqa: E402
from app.league_registry import load_leagues  # noqa: E402
from app.membership import grant_league  # noqa: E402
from app.models import User  # noqa: E402
from app.repository import get_repository  # noqa: E402
from app.web import app  # noqa: E402

FAILURES: list = []


def check(label: str, expected, actual) -> None:
    if expected == actual:
        print(f'  ok   {label}')
        return
    FAILURES.append(label)
    print(f'  FAIL {label}')
    print(f'       expected: {expected!r}')
    print(f'       actual:   {actual!r}')


def check_severity_buckets() -> None:
    """Every designation seen in the real leagues, plus one nobody emits."""
    print('\ninjury severity buckets')
    cases = [
        (None, None, None),
        ('Active', None, None),
        ('Active', 'Questionable', 'questionable'),
        ('Active', 'PUP', 'out'),
        ('Active', 'Sus', 'out'),
        ('Active', 'DNR', 'out'),
        ('Inactive', 'IR', 'out'),
        # An Injured Reserve roster status outranks a mild designation.
        ('Injured Reserve', 'Questionable', 'out'),
        # An unrecognized designation understates rather than inventing an
        # injury -- it is still shown, just in the milder colour.
        ('Active', 'Wobbly', 'questionable'),
    ]
    for status, injury, expected in cases:
        entry = RosterEntry(name='Test Player', status=status, injury_status=injury)
        check(f'status={status!r} injury={injury!r}', expected, entry.injury_severity)


def check_league_data() -> tuple:
    """Byes/injuries across every registered league's real rosters."""
    print('\nbye weeks resolve for every player who has an NFL team')
    seen_injury = False
    seen_bye = False
    for league_id in load_leagues():
        try:
            teams = get_repository(league_id).roster_teams()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f'  skip {league_id}: {type(exc).__name__}: {exc}')
            continue
        players = [p for team in teams for p in team.players]
        if not players:
            print(f'  skip {league_id}: no rostered players synced')
            continue
        # A player WITH an NFL team and no bye is the real bug. A player
        # without one is a free agent and correctly has none.
        missing = sorted({p.name for p in players if p.nfl_team and p.bye_week is None})
        check(f'{league_id}: every rostered player with a team has a bye', [], missing)
        seen_bye = seen_bye or any(p.bye_week for p in players)
        seen_injury = seen_injury or any(p.injury_severity for p in players)
    return seen_bye, seen_injury


def check_page(client) -> None:
    """The overview page renders both, for the caller's default league."""
    print('\nthe overview page')
    response = client.get('/')
    check('overview renders 200', 200, response.status_code)
    body = response.get_data(as_text=True)

    # Match the rendered ELEMENT, never a bare class name -- base.html's
    # inline <style> block carries every class on every page.
    check('bye badges rendered', True, 'class="bye"' in body)
    check('injury badges rendered', True, 'class="pill inj inj-' in body)

    # The macro is shared by the starters and bench tables; a row that lost
    # its bye would render the team cell with nothing after it. Cross-check
    # one real player end to end rather than trusting the class alone.
    repo = get_repository(next(iter(load_leagues())))
    players = [p for team in repo.roster_teams() for p in team.players]
    with_bye = next((p for p in players if p.bye_week and p.name in body), None)
    if with_bye is None:
        check('a player with a bye appears on the page', True, False)
    else:
        check(f'{with_bye.name} shows bye B{with_bye.bye_week}',
              True, f'>B{with_bye.bye_week}<' in body)


def main() -> int:
    init_db()

    slug = next((s for s, league in load_leagues().items() if league.platform == 'yahoo'), None)
    if slug is None:
        print('No Yahoo league in data/config/leagues.json -- run `python3 -m app leagues-init` first.')
        return 1

    with SessionLocal() as session:
        user = User(email='health@example.com')
        session.add(user)
        session.commit()
        user_id = user.id
    grant_league('health@example.com', slug, make_default=True)

    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(user_id)
        session['_fresh'] = True

    check_severity_buckets()
    seen_bye, seen_injury = check_league_data()

    print('\nthe real leagues carry the data at all')
    # Without these, every check above passes vacuously on empty rosters --
    # the same trap the matchups contract check documents.
    check('at least one player has a bye week', True, seen_bye)
    check('at least one player has an injury designation', True, seen_injury)

    check_page(client)

    print()
    if FAILURES:
        print(f'{len(FAILURES)} check(s) FAILED:')
        for label in FAILURES:
            print(f'  - {label}')
        return 1
    print('All roster-health checks passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
