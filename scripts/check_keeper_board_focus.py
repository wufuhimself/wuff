#!/usr/bin/env python3
"""Assert the keeper board stayed narrow, and that showing keepers changed nothing.

Two claims, both silent if they break:

1. The post-keeper draft board is unchanged. Kept players now stay IN
   `remaining_board` (flagged `isKeeper`, holding no `draftOrder`) so the page
   can dim them behind a Show/Hide keepers toggle. If that ever starts
   consuming pick numbers, every pick number on the page is wrong and nothing
   raises -- the board just quietly shifts. So: pick numbers must be gap-free
   from 1, and every kept row must carry no slot at all.

2. Per-user board customization is gone. The '▲/▼/↺' arrows, the "Customize my
   board" toggle and the /board/* endpoints were removed in favour of a future
   custom-rankings table; a leftover route or column would put the removed
   feature back on a page whose whole point is now being narrow.

    python3 scripts/check_keeper_board_focus.py

Exits non-zero on any failed expectation. Runs against the real Flask app and
a throwaway COPY of the local database (the frank-gore rosters this needs live
in the database, not data/raw/), like check_league_scoping.py /
check_scouting_gate.py.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_REAL_DB = Path(__file__).resolve().parent.parent / 'data' / 'wuff.db'
_TMP_DB = Path(tempfile.mkdtemp(prefix='wuff-keeperboard-')) / 'focus.db'
# A COPY of the real local database, not an empty one: the frank-gore rosters
# and draft history this check needs live in the database now (they moved out
# of the gitignored data/raw/ in 2026-08-12), so an empty schema has no keeper
# board to check. It is still throwaway -- the test user is written to the
# copy, and the real database is never opened for writing.
if _REAL_DB.exists():
    shutil.copyfile(_REAL_DB, _TMP_DB)
# Must be set before app.db is imported anywhere -- the engine is module-level.
os.environ['DATABASE_URL'] = f'sqlite:///{_TMP_DB}'
os.environ['WUFF_DISABLE_SCHEDULER'] = '1'

# pylint: disable=wrong-import-position
from app.db import SessionLocal, init_db  # noqa: E402
from app.keeper_service import keeper_board_state  # noqa: E402
from app.league_registry import load_leagues  # noqa: E402
from app.membership import grant_league  # noqa: E402
from app.models import User  # noqa: E402
from app.strategy import available_only  # noqa: E402
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


def main() -> int:  # pylint: disable=too-many-statements
    init_db()

    slug = next((s for s, league in load_leagues().items() if league.platform == 'yahoo'), None)
    if slug is None:
        print('No Yahoo league in data/config/leagues.json -- run `python3 -m app leagues-init` first.')
        return 1

    with SessionLocal() as session:
        user = User(email='owner@example.com')
        session.add(user)
        session.commit()
        user_id = user.id
    grant_league('owner@example.com', slug, make_default=True)

    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(user_id)
        session['_fresh'] = True

    state = keeper_board_state()
    if state['error']:
        print(f"Cannot check -- keeper board unavailable: {state['error']}")
        print('Needs a local database carrying the Yahoo rosters/rankings '
              '(`python3 -m app migrate-yahoo-data`, `refresh-free-rankings`).')
        return 1
    board = state['remaining_board']
    available = available_only(board)
    keepers = [row for row in board if row.get('isKeeper')]

    print('\nthe board keeps its post-keeper pick numbers')
    check('pick numbers are gap-free from 1', list(range(1, len(available) + 1)),
          [row.get('draftOrder') for row in available])
    check('no kept player holds a pick number', [],
          [row['playerName'] for row in keepers if row.get('draftOrder') is not None])
    check('every kept row names the team keeping it', [],
          [row['playerName'] for row in keepers if not row.get('keptBy')])
    # The truncation counts DRAFT SLOTS, not rows: counting rows would shrink
    # the draftable board by however many keepers ranked inside the cut.
    check('300 draftable players survive truncation', 300, len(available))
    check('the board carries keepers too (this is a keeper league)', True, len(keepers) > 0)

    print('\nkeepers sit at their ranking position, interleaved')
    ranks = [row.get('ranking') or 9999 for row in board]
    check('whole board still sorted by ranking', sorted(ranks), ranks)
    check('available_only() is exactly the non-keeper rows', len(board) - len(keepers), len(available))

    print('\nthe page')
    body = client.get('/keepers-board').get_data(as_text=True)
    check('page renders 200', 200, client.get('/keepers-board').status_code)
    # Match the rendered ELEMENT, never a bare class name -- base.html's inline
    # <style> block carries every class on every page (the trap
    # check_league_scoping.py documents).
    check('keeper rows rendered', True, 'class="keeper-row"' in body)
    # A kept row shows "Player — Team" beside the name and leaves the pick
    # column empty; there is no badge in the number cell any more.
    first_keeper = keepers[0]
    check('kept rows name the keeping team beside the player', True,
          f'— {first_keeper["keptBy"]}' in body)
    check('no kept badge in the pick column', False, '>kept</span>' in body)
    check('keepers hidden by default', True, 'class="table compact keepers-hidden"' in body)
    check('show/hide toggle rendered', True, 'id="keeper-visibility-toggle"' in body)
    check('customize-board toggle gone', False, 'id="board-edit-toggle"' in body)
    check('per-player nudge arrows gone', False, 'js-board-adjust' in body)
    check('the Mine column gone', False, 'adjust-col' in body)
    check('keeper toggle cards still there', True, 'js-keeper-toggle' in body)

    print('\nthe removed endpoints')
    for path in ('/board/adjust', '/board/reset'):
        check(f'{path} is gone', 404, client.post(path, data={'player': 'x', 'direction': 'up'}).status_code)

    print('\nthe AJAX mark path serves the same board')
    team = state['per_team'][0]['team']
    player = state['per_team'][0]['candidates'][0]['playerName']
    marked = client.post('/keepers-board/mark',
                         data={'team': team, 'player': player, 'checked': '0'},
                         headers={'Accept': 'application/json'})
    check('mark returns 200', 200, marked.status_code)
    rows_html = marked.get_json().get('boardRowsHtml', '')
    check('re-rendered rows still carry keeper rows', True, 'class="keeper-row"' in rows_html)
    check('re-rendered rows carry no nudge arrows', False, 'js-board-adjust' in rows_html)

    if FAILURES:
        print(f'\n{len(FAILURES)} check(s) failed:')
        for label in FAILURES:
            print(f'  - {label}')
        return 1
    print('\nAll checks passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
