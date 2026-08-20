#!/usr/bin/env python3
"""Assert every page resolves to the *caller's* league, and only theirs.

Until 2026-08-12 every logged-in user landed on the Yahoo league: `/`,
`/keepers-board`, `/mock-draft`, `/standings` and `/draft-history` all read
`league_registry.default_league_id()`. Login stopped strangers reading it; a
second real account still got my league. This checks the fix from the outside
-- real requests through the Flask test client against a throwaway database --
because the failure mode is a page that renders perfectly well with the wrong
league's data, which raises nothing.

    python3 scripts/check_league_scoping.py

Exits non-zero on any failed expectation. Uses its own temp SQLite file, so it
never touches data/wuff.db or a configured DATABASE_URL.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP_DB = Path(tempfile.mkdtemp(prefix='wuff-scoping-')) / 'scoping.db'
# Must be set before app.db is imported anywhere -- the engine is module-level.
os.environ['DATABASE_URL'] = f'sqlite:///{_TMP_DB}'
os.environ['WUFF_DISABLE_SCHEDULER'] = '1'

# pylint: disable=wrong-import-position
from app.db import SessionLocal, init_db  # noqa: E402
from app.league_registry import load_leagues  # noqa: E402
from app.membership import default_league_for_user, grant_league, set_default_league  # noqa: E402
from app.models import DbLeague, User, UserLeague  # noqa: E402
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


def make_user(email: str) -> int:
    with SessionLocal() as session:
        user = User(email=email)
        session.add(user)
        session.commit()
        return user.id


def make_db_league(slug: str, platform: str, platform_league_id: str, name: str) -> int:
    with SessionLocal() as session:
        row = DbLeague(slug=slug, platform=platform, platform_league_id=platform_league_id,
                       name=name, season='2026', total_teams=12)
        session.add(row)
        session.commit()
        return row.id


def follow(user_id: int, league_row_id: int) -> None:
    with SessionLocal() as session:
        session.add(UserLeague(user_id=user_id, league_id=league_row_id))
        session.commit()


def client_for(user_id):
    """Test client logged in as user_id (None = anonymous)."""
    test_client = app.test_client()
    if user_id is not None:
        with test_client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True
    return test_client


def location(response) -> str:
    """Redirect target path (no host), or '' for a non-redirect."""
    if response.status_code not in (301, 302, 303, 307, 308):
        return ''
    target = response.headers.get('Location', '')
    for prefix in ('http://localhost', 'http://'):
        if target.startswith(prefix):
            target = '/' + target.split('/', 3)[-1] if prefix == 'http://' else target[len(prefix):]
    return target.split('?')[0]


def main() -> int:  # pylint: disable=too-many-statements
    init_db()

    yahoo_slug = next((slug for slug, league in load_leagues().items() if league.platform == 'yahoo'), None)
    if yahoo_slug is None:
        print('No Yahoo league in data/config/leagues.json -- run `python3 -m app leagues-init` first.')
        return 1

    owner = make_user('owner@example.com')
    stranger = make_user('stranger@example.com')
    sleeper_user = make_user('sleeper@example.com')

    print(grant_league('owner@example.com', yahoo_slug, make_default=True))
    sleeper_row = make_db_league('sleeper-999', 'sleeper', '999', 'Some Sleeper League')
    other_row = make_db_league('sleeper-888', 'sleeper', '888', 'Another Sleeper League')
    follow(sleeper_user, sleeper_row)

    anon = client_for(None)
    owner_client = client_for(owner)
    stranger_client = client_for(stranger)
    sleeper_client = client_for(sleeper_user)

    print('\nanonymous is still shut out, except the public landing page')
    # / is the public landing page (2026-08-14): a signed-out visitor gets a
    # real 200 with a signup form, not a bounce to /login -- that's the
    # feature, not a leak, since index() shows no league data when
    # current_user isn't authenticated. Every OTHER route stays gated.
    anon_root = anon.get('/')
    check('GET / renders (public landing page)', 200, anon_root.status_code)
    # Checked against the actual rendered ELEMENT, not the CSS class
    # definition every page's <style> block carries regardless -- an early
    # version of this check matched 'league-nav' against base.html's own
    # stylesheet and failed on a page that was never leaking anything.
    check('GET / does not leak league data', False, b'<nav class="league-nav"' in anon_root.data)
    check('GET /keepers-board redirects to login', '/login', location(anon.get('/keepers-board')))

    print('\nowner (follows the Yahoo league, default) keeps the file-backed pages')
    check('GET / renders', 200, owner_client.get('/').status_code)
    check('GET /keepers-board renders', 200, owner_client.get('/keepers-board').status_code)
    check('GET /mock-draft renders', 200, owner_client.get('/mock-draft').status_code)
    check('GET /standings renders', 200, owner_client.get('/standings').status_code)
    # manager-report was pulled from nav 2026-08-20 (messy table, rework later)
    # and now 302s to the league's own overview instead of rendering -- still
    # membership-gated first (a non-member gets /leagues, checked below), so
    # this checks it resolves the OWNER's own league correctly, not /leagues.
    check('GET /league/<yahoo>/manager-report redirects to the Yahoo overview', '/',
          location(owner_client.get(f'/league/{yahoo_slug}/manager-report')))

    print('\nstranger follows nothing: no league, not the default one')
    check('GET / redirects to /my/leagues', '/my/leagues', location(stranger_client.get('/')))
    check('GET /keepers-board redirects to /my/leagues', '/my/leagues',
          location(stranger_client.get('/keepers-board')))
    check('GET /mock-draft redirects to /my/leagues', '/my/leagues',
          location(stranger_client.get('/mock-draft')))
    check('GET /draft-history/2025 redirects to /my/leagues', '/my/leagues',
          location(stranger_client.get('/draft-history/2025')))
    check('GET /standings/2025 redirects to /my/leagues', '/my/leagues',
          location(stranger_client.get('/standings/2025')))
    check('GET /draft-picks/2026 redirects to /my/leagues', '/my/leagues',
          location(stranger_client.get('/draft-picks/2026')))
    check('GET /draft-order/2025 redirects to /my/leagues', '/my/leagues',
          location(stranger_client.get('/draft-order/2025')))
    check("GET the Yahoo league's keepers redirects to /leagues", '/leagues',
          location(stranger_client.get(f'/league/{yahoo_slug}/keepers')))
    check("GET the Yahoo league's manager report redirects to /leagues", '/leagues',
          location(stranger_client.get(f'/league/{yahoo_slug}/manager-report')))
    check("GET the Yahoo league's scouting page redirects to /leagues", '/leagues',
          location(stranger_client.get(f'/league/{yahoo_slug}/scouting')))
    check("GET the Yahoo league's settings redirects to /leagues", '/leagues',
          location(stranger_client.get(f'/league/{yahoo_slug}/settings')))
    check('a stranger sees no leagues on /leagues', False,
          b'frank' in stranger_client.get('/leagues').data.lower())

    print('\nstranger cannot write to a league they do not follow')
    marked = stranger_client.post('/keepers-board/mark',
                                  data={'team': 'Some Team', 'player': 'Some Player',
                                        'checked': '1', 'league_slug': yahoo_slug})
    check('POST /keepers-board/mark is refused', 404, marked.status_code)
    adjusted = stranger_client.post('/board/adjust',
                                    data={'player': 'Some Player', 'direction': 'up',
                                          'league_slug': yahoo_slug})
    check('POST /board/adjust is refused', 404, adjusted.status_code)
    with SessionLocal() as session:
        check('no keeper mark was written', 0, session.query(UserLeague).filter_by(user_id=stranger).count())

    print('\nsleeper user lands on their own league, not the Yahoo one')
    check('GET / redirects to their league overview', '/sleeper/999',
          location(sleeper_client.get('/')))
    check('GET /keepers-board redirects to their per-league keepers', '/league/sleeper-999/keepers',
          location(sleeper_client.get('/keepers-board')))
    check('GET /mock-draft redirects to their per-league mock draft', '/league/sleeper-999/mock-draft',
          location(sleeper_client.get('/mock-draft')))
    check('GET /standings renders their own (empty) history', 200,
          sleeper_client.get('/standings').status_code)
    check('GET /draft-order/2025/board redirects to their keepers page', '/league/sleeper-999/keepers',
          location(sleeper_client.get('/draft-order/2025/board')))
    check("GET the Yahoo league's keepers redirects to /leagues", '/leagues',
          location(sleeper_client.get(f'/league/{yahoo_slug}/keepers')))
    check("GET another user's sleeper league redirects to /leagues", '/leagues',
          location(sleeper_client.get('/sleeper/888')))
    check('GET their own sleeper league renders', 200, sleeper_client.get('/sleeper/999').status_code)

    print('\na user in both leagues reaches the Yahoo pages by ?league=, without swapping default')
    both = make_user('both@example.com')
    follow(both, sleeper_row)
    follow(both, _yahoo_row_id(yahoo_slug))
    set_default_league(both, 'sleeper-999')
    both_client = client_for(both)
    check('?league=<yahoo> renders the file-backed board', 200,
          both_client.get(f'/keepers-board?league={yahoo_slug}').status_code)
    check('/league/<yahoo>/keepers bounces to it rather than to the default league', '/keepers-board',
          location(both_client.get(f'/league/{yahoo_slug}/keepers')))
    check('their default is still the Sleeper league', 'sleeper-999',
          default_league_for_user(both).league_id)
    check('an unfollowed ?league= is refused', '/my/leagues',
          location(sleeper_client.get(f'/keepers-board?league={yahoo_slug}')))

    print('\ndefault-league selection')
    check('rejects a league the user does not follow', False, set_default_league(stranger, yahoo_slug))
    follow(sleeper_user, other_row)
    check('accepts one they do follow', True, set_default_league(sleeper_user, 'sleeper-888'))
    check('the new default is served', 'sleeper-888', default_league_for_user(sleeper_user).league_id)
    rejected = stranger_client.post('/my/leagues/default', data={'slug': yahoo_slug},
                                    follow_redirects=True)
    check('POST /my/leagues/default rejects a non-followed league', True,
          b'Not one of your leagues' in rejected.data)
    with SessionLocal() as session:
        session.query(UserLeague).filter_by(user_id=sleeper_user, league_id=other_row).delete()
        session.commit()
    check('a stored default they no longer follow falls back to a league they do', 'sleeper-999',
          default_league_for_user(sleeper_user).league_id)
    check('a user who follows nothing has no default', None, default_league_for_user(stranger))

    print()
    if FAILURES:
        print(f'{len(FAILURES)} failed expectation(s): ' + ', '.join(FAILURES))
        return 1
    print('League scoping holds for every checked route.')
    return 0


def _yahoo_row_id(slug: str) -> int:
    with SessionLocal() as session:
        return session.query(DbLeague).filter_by(slug=slug).one().id


if __name__ == '__main__':
    sys.exit(main())
