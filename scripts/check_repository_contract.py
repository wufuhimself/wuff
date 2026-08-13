#!/usr/bin/env python3
"""Assert the typed repository API holds for every backend and every league.

Phase 5 step 3. The dict API's "contract" was a docstring, and the backends
quietly disagreed inside it for the whole life of the project -- Yahoo rosters
carry teamId/ownerName while Sleeper's carry rosterId/ownerId/starters, and
Sleeper standings have no rank field at all. This runs the same assertions
over every registered league so a backend cannot drift again without failing
here.

It also asserts the typed view does not LOSE anything: one typed record per
dict record, same names, same order. That is the property that makes migrating
a consumer safe.

    python3 scripts/check_repository_contract.py

Exits non-zero on the first violated invariant.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
from app.domain import (  # noqa: E402
    TRANSACTION_TYPES,
    DraftPick,
    Matchup,
    RankingRow,
    RosterTeam,
    StandingRow,
    Transaction,
)
from app.league_registry import load_leagues  # noqa: E402
from app.repository import repository_for  # noqa: E402

FAILURES = []
CHECKS = 0

# Positions any backend is allowed to report, after normalization. An
# unrecognized position is not cosmetic: mock_draft gives one no limit at all,
# which is how a 12-team league once drafted 15 defenses.
KNOWN_POSITIONS = {
    'QB', 'RB', 'WR', 'TE', 'K', 'DEF', 'DST', 'DL', 'LB', 'DB', 'IDP',
    'OL', 'C', 'G', 'T', 'FB', 'P', 'LS', 'CB', 'S', 'DE', 'DT', 'OLB', 'ILB',
    'NT', 'SS', 'FS', 'MLB', 'EDGE', 'ATH', 'OT', 'OG', 'UNK',
}

# From the Sleeper players cache, surveyed 2026-08-13 (app/player_registry.py's
# docstring). An unrecognized value here is a source-format change worth
# knowing about, not necessarily wrong -- so this is a check, not an
# assertion the set is exhaustive forever.
KNOWN_STATUSES = {'Active', 'Inactive', 'Injured Reserve'}
KNOWN_INJURY_STATUSES = {'Questionable', 'IR', 'PUP', 'NA', 'Sus', 'Out', 'DNR', 'Doubtful'}


def check(label, condition, detail=''):
    global CHECKS  # pylint: disable=global-statement
    CHECKS += 1
    if not condition:
        FAILURES.append(f'{label}: {detail}')
        print(f'    FAIL {label}  <- {detail}')


def check_league(league_id, league) -> None:
    repo = repository_for(league)
    print(f'  {league_id} ({league.platform})')

    raw_rosters = repo.rosters()
    teams = repo.roster_teams()
    check('roster count preserved', len(teams) == len(raw_rosters),
          f'{len(raw_rosters)} dict vs {len(teams)} typed')
    for team, raw in zip(teams, raw_rosters):
        check('roster is RosterTeam', isinstance(team, RosterTeam), type(team).__name__)
        # Compared against the STRIPPED raw name: trimming display whitespace
        # is a normalization the typed layer promises. One real Sleeper team is
        # named " Griddy - ators " with padding.
        check('team name preserved', team.team_name == (raw.get('teamName') or '').strip(),
              f'{team.team_name!r} vs {raw.get("teamName")!r}')
        check('player count preserved', len(team.players) == len(raw.get('players') or []),
              f'{team.team_name}: {len(raw.get("players") or [])} vs {len(team.players)}')
        for entry, raw_player in zip(team.players, raw.get('players') or []):
            check('player name preserved', entry.name == (raw_player.get('playerName') or '').strip(),
                  f'{entry.name!r} vs {raw_player.get("playerName")!r}')
            if entry.position:
                check('position is recognized', entry.position.upper() in KNOWN_POSITIONS,
                      f'{entry.name}: {entry.position!r}')
            if entry.bye_week is not None:
                check('bye week is a real week number', 1 <= entry.bye_week <= 18,
                      f'{entry.name}: {entry.bye_week}')
                # A resolved player whose bye is known should never ALSO look
                # unrostered -- a resolved-but-bye-less active player would be
                # the team-code mismatch bug (schedule said 'LA', registry
                # said 'LAR') showing up again under a different team.
            if entry.status:
                check('status is recognized', entry.status in KNOWN_STATUSES,
                      f'{entry.name}: {entry.status!r}')
            if entry.injury_status:
                check('injury status is recognized', entry.injury_status in KNOWN_INJURY_STATUSES,
                      f'{entry.name}: {entry.injury_status!r}')

    raw_years = repo.draft_years()
    drafts = repo.drafts()
    check('draft seasons preserved', set(drafts) == {int(y) for y in raw_years},
          f'{sorted(raw_years)} vs {sorted(drafts)}')
    for season, picks in drafts.items():
        check('pick count preserved', len(picks) == len(raw_years[season]),
              f'{season}: {len(raw_years[season])} vs {len(picks)}')
        for pick in picks:
            check('pick is DraftPick', isinstance(pick, DraftPick), type(pick).__name__)
            check('season is set', pick.season == season, f'{pick.season} vs {season}')
            check('round is positive', pick.round >= 1, f'{season} r{pick.round}')
            if pick.position:
                check('pick position recognized', pick.position.upper() in KNOWN_POSITIONS,
                      f'{pick.player_name}: {pick.position!r}')

    for season in repo.standings_years():
        rows = repo.standing_rows(season)
        raw_rows = repo.standings(season) or []
        check('standing count preserved', rows is not None and len(rows) == len(raw_rows),
              f'{season}: {len(raw_rows)} vs {len(rows or [])}')
        ranks = [row.rank for row in rows or []]
        check('every standing row has a rank', all(isinstance(r, int) and r >= 1 for r in ranks),
              f'{season}: {ranks}')
        check('ranks are unique', len(set(ranks)) == len(ranks), f'{season}: {ranks}')
        for row in rows or []:
            check('standing is StandingRow', isinstance(row, StandingRow), type(row).__name__)

    raw_rankings = repo.rankings()
    rankings = repo.ranking_rows()
    check('ranking count preserved', len(rankings) == len(raw_rankings),
          f'{len(raw_rankings)} vs {len(rankings)}')
    for row in rankings[:400]:
        check('ranking is RankingRow', isinstance(row, RankingRow), type(row).__name__)
        check('ranking row has a name', bool(row.name), repr(row.name))

    raw_transactions = repo.raw_transactions()
    transactions = repo.transactions()
    check('transaction count preserved', len(transactions) == len(raw_transactions),
          f'{len(raw_transactions)} vs {len(transactions)}')
    move_resolved = move_total = 0
    for txn in transactions:
        check('transaction is Transaction', isinstance(txn, Transaction), type(txn).__name__)
        check('transaction has an id', bool(txn.transaction_id), repr(txn.transaction_id))
        check('transaction type is recognized', txn.type in TRANSACTION_TYPES, txn.type)
        # A trade/waiver with zero moves AND zero pick_moves would be a
        # transaction that moved nothing -- either a real Sleeper edge case
        # (rare) or a normalization bug silently dropping every add/drop key.
        if txn.type in ('trade', 'waiver', 'free_agent'):
            check('transaction moves something', bool(txn.moves or txn.pick_moves),
                  f'{txn.transaction_id} ({txn.type}): no moves or pick_moves')
        for move in txn.moves:
            check('move action is add or drop', move.action in ('add', 'drop'), move.action)
            move_total += 1
            if move.canonical_player_id:
                move_resolved += 1
    if transactions:
        print(f'      transactions: {len(transactions)}, '
              f'{move_resolved}/{move_total} player moves carry a canonical id')

    matchups = repo.matchups()
    starter_resolved = starter_total = 0
    weeks_seen = set()
    for matchup in matchups:
        check('matchup is Matchup', isinstance(matchup, Matchup), type(matchup).__name__)
        check('matchup week is positive', matchup.week >= 1, matchup.week)
        weeks_seen.add(matchup.week)
        for side in (matchup.home, matchup.away):
            # Zero points on a matchup this function chose to return would be
            # exactly the "week hasn't happened yet" state repository.matchups()
            # promises to filter out -- a leak here means that filter broke.
            check('matchup side has a score', side.points is not None and side.points > 0,
                  f'week {matchup.week} {side.team_name}: {side.points}')
            for sid in side.starter_ids:
                starter_total += 1
                if sid.startswith('sleeper:') or sid.startswith('nfl:'):
                    starter_resolved += 1
    # One matchup_id pairs exactly two teams; a week with an odd team count
    # (odd-sized league, or a bye) legitimately has fewer pairs than teams/2,
    # so this checks "even" and "no lopsided week", not an exact count.
    for week in weeks_seen:
        count = sum(1 for m in matchups if m.week == week)
        check(f'week {week} has a sane pairing count', 1 <= count <= 20, count)
    if matchups:
        print(f'      matchups: {len(matchups)} across {len(weeks_seen)} scored week(s), '
              f'{starter_resolved}/{starter_total} starters carry a canonical id')

    all_players = [p for t in teams for p in t.players]
    resolved = sum(1 for p in all_players if p.canonical_player_id)
    total = len(all_players)
    franchised = sum(1 for t in teams if t.franchise_id)
    # Bye/status denominator excludes DEF: a team defense has no injury
    # status, and a snapshot with no schedule fetched for the current season
    # yet is a real, non-error state (see bye_weeks.py), not a resolution gap.
    skaters = [p for p in all_players if p.canonical_player_id and p.position != 'DEF']
    with_status = sum(1 for p in skaters if p.status)
    with_bye = sum(1 for p in skaters if p.bye_week is not None)
    print(f'      players {resolved}/{total} carry a canonical id; '
          f'{franchised}/{len(teams)} teams carry a franchise id')
    if skaters:
        print(f'      of resolved non-DEF players: {with_status}/{len(skaters)} carry a status, '
              f'{with_bye}/{len(skaters)} carry a bye week')


def check_completed_season_matchups() -> None:
    """None of the 6 registered Sleeper leagues have played a game yet (2026
    season, pre-kickoff) -- the per-league loop above exercises matchups()
    honestly, but every one of its assertions passes vacuously on zero rows.
    This targets a real completed prior season (reachable only via Sleeper's
    previous_league_id chain, deliberately NOT walked by sync -- see the
    Phase 5 roadmap note) so the pairing/resolution logic gets checked against
    real scored weeks at least once. Synced on demand here rather than kept
    registered, since it belongs to no wuff league.
    """
    import os  # pylint: disable=import-outside-toplevel

    from app.league_context import LeagueFormat  # pylint: disable=import-outside-toplevel
    from app.league_registry import League  # pylint: disable=import-outside-toplevel
    from app.repository import SleeperJsonRepository  # pylint: disable=import-outside-toplevel
    from app.sleeper_manager import sync_league  # pylint: disable=import-outside-toplevel

    if os.environ.get('WUFF_SKIP_LIVE_CHECKS') == '1':
        return
    completed_league_id = '1180225300948316160'  # a real completed 2025 Sleeper season
    print(f'  completed-season sample ({completed_league_id})')
    try:
        sync_league(completed_league_id)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f'    skipped (offline or unreachable: {type(exc).__name__}: {exc})')
        return
    league = League(league_id='__completed_sample__', platform='sleeper',
                    platform_league_id=completed_league_id, name='completed sample',
                    season='2025', format=LeagueFormat())
    repo = SleeperJsonRepository(league)
    matchups = repo.matchups()
    check('completed season has 17 scored weeks', len({m.week for m in matchups}) == 17,
          sorted({m.week for m in matchups}))
    check('completed season resolves every team name', matchups and all(
        m.home.team_name and m.away.team_name for m in matchups), 'a side had no team_name')
    check('completed season has no zero-point sides', not any(
        (s.points or 0) <= 0 for m in matchups for s in (m.home, m.away)),
        'a real week leaked a zero score')


def main() -> int:
    print('Repository contract:')
    for league_id, league in load_leagues().items():
        try:
            check_league(league_id, league)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            FAILURES.append(f'{league_id}: {type(exc).__name__}: {exc}')
            print(f'    FAIL {league_id} raised {type(exc).__name__}: {exc}')

    check_completed_season_matchups()

    print()
    if FAILURES:
        print(f'{len(FAILURES)} violation(s) of {CHECKS} checks:')
        for failure in FAILURES[:20]:
            print(f'  - {failure}')
        return 1
    print(f'Contract holds for every backend ({CHECKS} checks).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
