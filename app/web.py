import json
import logging
import os
from datetime import datetime

from flask import Flask, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from . import espn_manager, sleeper_client
from .auth import (
    generate_login_token,
    get_or_create_user,
    init_auth,
    login_manager,
    login_send_allowed,
    verify_login_token,
)
from .board_service import bump_adjustment, clear_adjustment, clear_all_adjustments
from .crypto import encrypt_value
from .db import SessionLocal, init_db
from .draft_analysis import (
    draft_slot_vs_final_rank,
    position_in_round_vs_final_rank,
    summarize_draft_slot_correlation,
    summarize_position_in_round,
)
from .draft_history import keeper_slot_picks, live_draft_picks
from .draft_patterns import (
    position_mix_by_round,
    position_rank_pick_targets,
    position_timing,
    resolved_picks,
)
from .free_rankings import refresh_free_rankings
from .keeper_service import (
    keeper_board_state,
    load_keeper_marks,
    log_team_keeper_forecast,
    team_pick_numbers,
)
from .league_context import load_league_format
from .league_service import resolve_league, save_league_rules
from .mailer import send_magic_link
from .manager_report import manager_report_card
from .membership import (
    default_league_for_user,
    followed_league_rows,
    followed_leagues,
    set_default_league,
    user_follows,
    user_follows_platform_league,
)
from .models import DbLeague, EspnCredential, KeeperMark, SyncRun, UserLeague
from .paths import CONFIG_DIR, YAHOO_LEAGUE_ROSTERS_JSON
from .repository import repository_for
from .sleeper_manager import (
    load_sleeper_leagues_config,
    load_synced_drafts,
    load_synced_league,
    load_synced_rosters,
)
from .standings import current_team_names, draft_order_from_standings, snake_draft_order
from .player_registry import normalize_name
from .strategy import league_keeper_board
from .sync_scheduler import ensure_scheduler_started, queue_league_sync

# The root logger defaults to WARNING, which hides the background jobs'
# progress lines under gunicorn -- and those jobs fetch the data whose
# absence degrades pages silently, so their logs are the only way to tell a
# skipped refresh from a working one. LOG_LEVEL overrides.
logging.basicConfig(
    level=os.environ.get('LOG_LEVEL', 'INFO').upper(),
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)

app = Flask(__name__, template_folder='templates', static_folder='static')
# Dev fallback only — any deploy sets a real SECRET_KEY (docs/roadmap.md Phase 4).
app.secret_key = os.environ.get('SECRET_KEY', 'dev-only-not-a-secret')
init_db()
init_auth(app)

LEAGUE_RULES_FILE = CONFIG_DIR / 'league_rules.json'


@app.before_request
def _start_background_sync():
    # Idempotent fast path after first call; disabled via WUFF_DISABLE_SCHEDULER=1.
    ensure_scheduler_started()


# Endpoints reachable without an account. Everything else -- including the
# default league's dashboard, keeper board and mock draft -- requires login.
PUBLIC_ENDPOINTS = frozenset({'login', 'login_verify', 'logout', 'static'})


@app.before_request
def _require_login():
    """Gate the whole app rather than decorating routes one by one.

    Every page except /login served a real league's rosters, standings and
    draft history to anonymous visitors -- a leftover from when wuff was a
    single-user local tool and there was no one else to hide it from. Doing
    this as an allowlist instead of ~30 @login_required decorators means a
    newly added route is private by default; forgetting a decorator would
    leak silently, which is the failure mode worth designing out.
    """
    if request.endpoint in PUBLIC_ENDPOINTS or current_user.is_authenticated:
        return None
    return login_manager.unauthorized()


def _league_href(platform: str, platform_league_id: str) -> str:
    if platform == 'sleeper':
        return f'/sleeper/{platform_league_id}'
    if platform == 'espn':
        return f'/espn/{platform_league_id}'
    return '/'


def _current_default_league():
    """The league this user's un-scoped pages (/, /keepers-board, /mock-draft,
    /standings, /draft-history) resolve to. None when they follow no league --
    there is no global fallback any more, see app/membership.py."""
    if not current_user.is_authenticated:
        return None
    return default_league_for_user(current_user.id)


def _yahoo_page_league():
    """League for the file-backed Yahoo pages (/keepers-board, /mock-draft).

    `?league=<slug>` when given -- so a user whose default is a Sleeper league
    can still open the Yahoo league they follow, instead of bouncing between
    /league/<slug>/keepers and /keepers-board forever -- otherwise their
    default league. None means "not yours / no league"."""
    slug = request.args.get('league', '').strip()
    if slug:
        return _member_league(slug)
    return _current_default_league()


def _default_repo():
    """Repository for this user's default league, or None when they have none.

    The pages that read a league's own history (/standings, /draft-history,
    /draft-picks, /draft-order) go through the repository interface, so they
    serve whichever league is the caller's -- they used to hardcode
    get_repository(), i.e. the Yahoo league, for everyone."""
    league = _current_default_league()
    return repository_for(league) if league is not None else None


def _no_league_redirect():
    return redirect(url_for(
        'my_leagues',
        message='No league selected yet — import one, or ask for access to an existing league.',
    ))


def _member_league(league_id: str):
    """A league by slug, but only if the current user follows it.

    None covers both "no such league" and "not one of yours", deliberately
    indistinguishable: every caller redirects to /leagues, so an outsider
    can't probe which slugs exist."""
    league = resolve_league(league_id)
    if league is None or not current_user.is_authenticated or not user_follows(current_user.id, league):
        return None
    return league


def _is_file_backed_yahoo(league) -> bool:
    """The original single-league Yahoo setup, whose keeper board/format come
    from the local config files rather than from DbLeague.rules_json."""
    return league is not None and league.platform == 'yahoo'


def _board_state_args(league):
    """(league, include_file_prefs) for keeper_service.keeper_board_state().

    The Yahoo league keeps the league=None path it has always used (file-based
    format + keeper prefs), so its board math is untouched by this scoping."""
    if _is_file_backed_yahoo(league):
        return None, True
    return league, False


@app.context_processor
def _inject_league_context():
    league = _current_default_league()
    return {
        'default_league_name': league.name if league is not None else 'My leagues',
        # The shared pages (/standings, /draft-history, ...) now serve whichever
        # league is the caller's, so the platform tag can't be a literal 'yahoo'.
        'default_league_platform': league.platform if league is not None else '',
    }


def _structure_yahoo_roster(raw_players: list) -> tuple:
    """Split a Yahoo roster snapshot into (starterSlots, bench) the same way
    _structure_rosters() does for Sleeper/ESPN: starters in lineup order if the
    snapshot has live selectedPosition data (post-draft), otherwise every
    player sorted by position into a single 'bench' list (pre-draft -- there's
    no lineup yet, just a roster)."""
    # Some saved roster snapshots have a "player Notes" suffix left over from
    # parsing pasted Yahoo text (see strategy.py's league_keeper_board, which
    # strips the same artifact) -- trim it here too so display names match.
    players = [{**p, 'playerName': str(p.get('playerName', '')).replace('player Notes', '').strip()}
               for p in raw_players]
    position_sort = {'QB': 0, 'RB': 1, 'WR': 2, 'TE': 3, 'K': 4, 'DEF': 5}
    starters = [p for p in players if p.get('selectedPosition') and p.get('selectedPosition') != 'BN']
    if not starters:
        bench = sorted(players, key=lambda p: (position_sort.get((p.get('position') or '').upper(), 9),
                                                p.get('playerName') or ''))
        return [], bench
    starter_ids = {p.get('playerId') for p in starters}
    bench = [p for p in players if p.get('playerId') not in starter_ids]
    bench.sort(key=lambda p: (position_sort.get((p.get('position') or '').upper(), 9), p.get('playerName') or ''))
    starter_slots = [(p.get('selectedPosition') or p.get('position') or '', p) for p in starters]
    return starter_slots, bench


@app.route('/')
def index():
    league = _current_default_league()
    if league is None:
        return _no_league_redirect()
    if not _is_file_backed_yahoo(league):
        # Sleeper/ESPN leagues have their own overview page; this dashboard is
        # built on the Yahoo snapshot shape (standings + parsed rosters).
        return redirect(_league_href(league.platform, league.platform_league_id))
    repo = repository_for(league)

    available_years = repo.standings_years()
    if not available_years:
        return render_template('dashboard.html', active='dashboard', league=league,
                                message=request.args.get('message', ''), standings_year=None,
                                standings_rows=[], round1_order=[], error=None)

    standings_year = available_years[0]
    standings = repo.standings(standings_year)
    league_rosters = repo.rosters()
    rosters_by_team = {
        str(r.get('teamName', '')).rsplit(' - ', maxsplit=1)[-1]: r.get('players') or []
        for r in league_rosters
    }

    aliases = current_team_names(standings) if standings else {}
    standings_rows = []
    for row in sorted(standings or [], key=lambda r: r.get('rank') or 999):
        display_name = aliases.get(row['team'], row['team'])
        starter_slots, bench = _structure_yahoo_roster(rosters_by_team.get(display_name, []))
        standings_rows.append({**row, 'displayName': display_name,
                                'starterSlots': starter_slots, 'bench': bench})

    round1_order = [aliases.get(name, name) for name in draft_order_from_standings(standings)] if standings else []

    return render_template(
        'dashboard.html', active='dashboard', league=league,
        message=request.args.get('message', ''), standings_year=standings_year,
        standings_rows=standings_rows, round1_order=round1_order, error=None,
    )


@app.route('/actions/refresh-rankings', methods=['POST'])
def refresh_rankings():
    """Pull fresh PPR ADP/rankings (FFC market ADP + Sleeper depth tail --
    Sleeper has no public ADP endpoint of its own, see free_rankings.py's
    module docstring) and write them as the app's working rankings board.
    Also runs automatically once a day via sync_scheduler; this is the
    on-demand trigger -- lives on /keepers-board and /mock-draft (?next=
    picks which page the redirect lands back on)."""
    next_view = 'mock_draft_view' if request.args.get('next') == 'mock-draft' else 'keepers_board_view'
    try:
        summary = refresh_free_rankings(scoring='ppr')
    except (RuntimeError, ValueError) as exc:
        return redirect(url_for(next_view, message=f'Rankings refresh failed: {exc}'))
    return redirect(url_for(next_view, message=(
        f"Refreshed {summary['total']} rankings ({summary['ffc']} FFC ADP, "
        f"{summary['sleeperTail']} Sleeper depth)."
    )))


@app.route('/keepers-board')
def keepers_board_view():
    league = _yahoo_page_league()
    if league is None:
        return _no_league_redirect()
    if not _is_file_backed_yahoo(league):
        return redirect(url_for('league_keepers', league_id=league.league_id))

    state = keeper_board_state(user_id=current_user.id if current_user.is_authenticated else None)
    if state['error']:
        return render_template('keepers_board.html', active='keepers-board', per_team=[], remaining_board=[],
                             error=state['error'], message=request.args.get('message', ''))

    repo = state['repo']
    league_format = state['league_format']
    per_team = state['per_team']
    remaining_board = state['remaining_board']
    include_marks = state['include_marks']

    teams = league_format.teams if league_format else 12
    live_rounds = 13

    available_years = repo.standings_years()
    round1_order = None
    origins_by_team = None
    if available_years:
        standings = repo.standings(available_years[0])
        if standings:
            aliases = current_team_names(standings)
            round1_order = [aliases.get(name, name) for name in draft_order_from_standings(standings)]
            origins_by_team = repo.draft_pick_origins(available_years[0] + 1)

    # Dropdown must use the same team names the pick math keys on (last season's standings,
    # normalized to current display names), not raw historical names -- team display names can
    # change year to year (see teamNames2025Note in league_rules.json) while the 12 manager slots don't.
    team_names = round1_order or ([entry['team'] for entry in per_team] if per_team else [])

    my_team = request.args.get('team') or (
        json.loads(LEAGUE_RULES_FILE.read_text()).get('myTeam', {}).get('displayName2025')
        if LEAGUE_RULES_FILE.exists() else None
    ) or (team_names[0] if team_names else None)

    if round1_order and my_team:
        my_picks = team_pick_numbers(my_team, round1_order, live_rounds, teams, origins_by_team)
    else:
        my_picks = set()

    for row in remaining_board:
        row['isMyPick'] = row.get('draftOrder') in my_picks
        row['round'] = ((row.get('draftOrder', 1) - 1) // teams) + 1

    return render_template(
        'keepers_board.html', active='keepers-board', per_team=per_team,
        # Feeds the cards'/board rows' data-league-slug, so the mark and adjust
        # POSTs name the league explicitly instead of relying on the poster's
        # default -- which may be a different league entirely when this page was
        # reached via ?league=. (base.html's nav ignores it for active pages
        # in the global tool list, so the chrome is unchanged.)
        league_slug=league.league_id,
        remaining_board=remaining_board, keeper_count=state['keeper_count'],
        keeper_forecasts=state['keeper_forecasts'], keeper_impact=state['keeper_impact'],
        my_team=my_team, team_names=team_names, error=None,
        keeper_marks=include_marks, message=request.args.get('message', ''),
        can_adjust=current_user.is_authenticated,
        has_adjustments=any(row.get('userOffset') for row in remaining_board),
    )


@app.route('/keepers-board/mark', methods=['POST'])
def keeper_mark():
    """Toggle one player's keeper checkbox for one team. `checked` is the
    desired end state (the box the user just clicked into); the server infers
    whether that requires an include row, an exclude row, or clearing any
    existing override, by comparing against the player's current
    algorithm-computed status for that team. Returns JSON with pre-rendered
    HTML fragments for the pieces of the page that changed, so the client can
    patch the DOM without a reload."""
    team = request.form.get('team', '').strip()
    player = request.form.get('player', '').strip()
    checked = request.form.get('checked', '').strip() == '1'
    league_slug = request.form.get('league_slug', '').strip()

    league = _member_league(league_slug) if league_slug else _current_default_league()
    if league is None:
        return {'error': 'Unknown league.'}, 404

    if not team or not player:
        return {'error': 'Missing team or player.'}, 400

    platform, platform_league_id = league.platform, league.platform_league_id
    board_league, include_file_prefs = _board_state_args(league)

    state_before = keeper_board_state(board_league, include_file_prefs=include_file_prefs)
    if state_before['error']:
        return {'error': state_before['error']}, 409

    team_entry = next((t for t in state_before['per_team'] if t['team'] == team), None)
    was_auto_chosen = bool(team_entry) and any(
        normalize_name(c.get('playerName', '')) == normalize_name(player) for c in team_entry['chosen']
    )
    chosen_count = len(team_entry['chosen']) if team_entry else 0

    if checked and not was_auto_chosen and chosen_count >= state_before['keeper_count']:
        # Team's already at its keeper cap -- silently no-op rather than
        # reject with an error; re-render exactly what's already there so the
        # click just does nothing instead of surfacing a warning.
        return {
            'impactHtml': render_template('_partials/keeper_impact.html', keeper_impact=state_before['keeper_impact']),
            'boardRowsHtml': render_template('_partials/draft_board_rows.html',
                                            remaining_board=state_before['remaining_board'],
                                            league_slug=league_slug,
                                            can_adjust=current_user.is_authenticated),
            'candidateCardsHtml': render_template(
                '_partials/keeper_candidate_cards.html', per_team=state_before['per_team'],
                league_slug=league_slug, keeper_count=state_before['keeper_count'],
            ),
        }

    already_has_marks = bool(
        (state_before['include_marks'] or {}).get(team) or (state_before['exclude_marks'] or {}).get(team)
    )

    with SessionLocal() as session:
        # First time this team is touched: the algorithm's current auto-picks
        # (other than the one being toggled right now) need to become real
        # `include` rows, not just implied by "nobody's excluded them yet" --
        # otherwise the next computation runs with stop_auto_fill=True and
        # silently drops them (they were never auto-fill-eligible OR
        # explicitly included, so they'd vanish instead of staying kept).
        if not already_has_marks and team_entry:
            for other in team_entry['chosen']:
                if normalize_name(other.get('playerName', '')) == normalize_name(player):
                    continue
                session.add(KeeperMark(platform=platform, platform_league_id=platform_league_id,
                                       team_name=team, player_name=other['playerName'], action='include'))

        existing = (
            session.query(KeeperMark)
            .filter_by(platform=platform, platform_league_id=platform_league_id,
                       team_name=team, player_name=player)
            .one_or_none()
        )
        if checked == was_auto_chosen:
            # Toggling back to the algorithm's own answer -- clear any override.
            if existing is not None:
                session.delete(existing)
        else:
            action = 'include' if checked else 'exclude'
            if existing is not None:
                existing.action = action
            else:
                session.add(KeeperMark(platform=platform, platform_league_id=platform_league_id,
                                       team_name=team, player_name=player, action=action))
        session.commit()

    state_after = keeper_board_state(board_league, include_file_prefs=include_file_prefs)
    if state_after['error']:
        return {'error': state_after['error']}, 409

    log_team_keeper_forecast(state_after, team, platform, platform_league_id)

    return {
        'impactHtml': render_template('_partials/keeper_impact.html', keeper_impact=state_after['keeper_impact']),
        'boardRowsHtml': render_template('_partials/draft_board_rows.html',
                                        remaining_board=state_after['remaining_board'],
                                        league_slug=league_slug,
                                        can_adjust=current_user.is_authenticated),
        'candidateCardsHtml': render_template(
            '_partials/keeper_candidate_cards.html', per_team=state_after['per_team'],
            league_slug=league_slug, keeper_count=state_after['keeper_count'],
        ),
    }


@app.route('/board/adjust', methods=['POST'])
def board_adjust():
    """Nudge one player up/down on the caller's own board, or reset them.

    Per-user, so it needs a login -- unlike keeper marks, which are shared
    per-league. `direction` is 'up'/'down' (by `spots`, default 1) or 'reset'.
    Returns the re-rendered board rows so the client can patch in place."""
    if not current_user.is_authenticated:
        return {'error': 'Log in to keep your own board adjustments.'}, 401

    player = request.form.get('player', '').strip()
    direction = request.form.get('direction', '').strip()
    league_slug = request.form.get('league_slug', '').strip()
    try:
        spots = max(1, min(50, int(request.form.get('spots', 1))))
    except (TypeError, ValueError):
        spots = 1

    if not player or direction not in ('up', 'down', 'reset'):
        return {'error': 'Missing player or direction.'}, 400

    league = _member_league(league_slug) if league_slug else _current_default_league()
    if league is None:
        return {'error': 'Unknown league.'}, 404

    platform, platform_league_id = league.platform, league.platform_league_id
    board_league, include_file_prefs = _board_state_args(league)

    if direction == 'reset':
        clear_adjustment(current_user.id, platform, platform_league_id, player)
    else:
        bump_adjustment(current_user.id, platform, platform_league_id, player,
                        spots if direction == 'up' else -spots)

    state = keeper_board_state(board_league, include_file_prefs=include_file_prefs, user_id=current_user.id)
    if state['error']:
        return {'error': state['error']}, 409
    return {
        'boardRowsHtml': render_template('_partials/draft_board_rows.html',
                                         remaining_board=state['remaining_board'],
                                         league_slug=league_slug, can_adjust=True),
    }


@app.route('/board/reset', methods=['POST'])
def board_reset():
    """Drop every manual adjustment this user has on a league's board."""
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    league_slug = request.form.get('league_slug', '').strip()
    league = _member_league(league_slug) if league_slug else _current_default_league()
    if league is None:
        return _no_league_redirect()
    removed = clear_all_adjustments(current_user.id, league.platform, league.platform_league_id)
    message = f'Reset {removed} board adjustment(s).' if removed else 'No board adjustments to reset.'
    if _is_file_backed_yahoo(league):
        return redirect(url_for('keepers_board_view', league=league.league_id, message=message))
    return redirect(url_for('league_keepers', league_id=league.league_id, message=message))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('my_leagues'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        if '@' not in email:
            return render_template('login.html', active='login', error='Enter a valid email address.', sent=False)
        if not login_send_allowed(email):
            return render_template(
                'login.html', active='login', sent=False,
                error='A login link was already sent recently — check your inbox, or wait a minute to resend.',
            )
        token = generate_login_token(app.secret_key, email)
        login_url = url_for('login_verify', token=token, _external=True)
        send_magic_link(email, login_url)
        return render_template('login.html', active='login', error=None, sent=True)
    return render_template('login.html', active='login', error=None, sent=False)


@app.route('/login/verify/<token>')
def login_verify(token):
    email = verify_login_token(app.secret_key, token)
    if email is None:
        return render_template(
            'login.html', active='login', sent=False,
            error='That login link is invalid or expired. Enter your email to get a new one.',
        )
    user = get_or_create_user(email)
    login_user(user, remember=True)
    return redirect(url_for('my_leagues'))


@app.route('/logout', methods=['POST'])
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/my/leagues')
@login_required
def my_leagues():
    rows = followed_league_rows(current_user.id)
    default_league = _current_default_league()
    default_slug = default_league.league_id if default_league is not None else None
    with SessionLocal() as session:
        entries = []
        for row in rows:
            last_run = (
                session.query(SyncRun)
                .filter_by(platform=row.platform, platform_league_id=row.platform_league_id)
                .order_by(SyncRun.started_at.desc())
                .first()
            )
            entries.append({
                'name': row.name,
                'slug': row.slug,
                'platform': row.platform,
                'platformLeagueId': row.platform_league_id,
                'season': row.season,
                'teams': row.total_teams,
                'href': _league_href(row.platform, row.platform_league_id),
                'isDefault': row.slug == default_slug,
                'lastSyncAt': last_run.started_at.strftime('%Y-%m-%d %H:%M UTC') if last_run else None,
                'lastSyncStatus': last_run.status if last_run else None,
            })
    return render_template(
        'my_leagues.html', active='my-leagues', leagues=entries,
        message=request.args.get('message', ''),
    )


@app.route('/my/leagues/default', methods=['POST'])
@login_required
def my_league_set_default():
    """Pick which league the un-scoped pages resolve to. Rejects leagues the
    user doesn't follow (see membership.set_default_league) -- this must not
    double as a way to claim access to someone else's league."""
    slug = request.form.get('slug', '').strip()
    if set_default_league(current_user.id, slug):
        return redirect(url_for('my_leagues', message='Default league updated.'))
    return redirect(url_for('my_leagues', message='Not one of your leagues.'))


@app.route('/my/leagues/sync/<platform_league_id>', methods=['POST'])
@login_required
def my_league_sync(platform_league_id: str):
    with SessionLocal() as session:
        followed = (
            session.query(DbLeague)
            .join(UserLeague, UserLeague.league_id == DbLeague.id)
            .filter(UserLeague.user_id == current_user.id,
                    DbLeague.platform_league_id == platform_league_id)
            .one_or_none()
        )
    if followed is None:
        return redirect(url_for('my_leagues', message='Not one of your leagues.'))
    queued = queue_league_sync(platform_league_id, followed.platform)
    note = 'Sync started in background.' if queued else 'Synced.'
    return redirect(url_for('my_leagues', message=note))


@app.route('/my/onboard', methods=['GET', 'POST'])
@login_required
def onboard():
    default_season = str(datetime.now().year)
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        season = request.form.get('season', '').strip() or default_season
        if not username:
            return render_template('onboard.html', active='my-leagues', discovered=None,
                                   username='', season=season, error='Enter a Sleeper username.')
        try:
            sleeper_user = sleeper_client.get_user(username)
            found = sleeper_client.get_user_leagues(sleeper_user['user_id'], season)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return render_template('onboard.html', active='my-leagues', discovered=None,
                                   username=username, season=season,
                                   error=f'Sleeper lookup failed: {exc}')
        discovered = [
            {
                'leagueId': entry.get('league_id'),
                'name': entry.get('name'),
                'season': entry.get('season'),
                'totalRosters': entry.get('total_rosters'),
            }
            for entry in found
        ]
        return render_template('onboard.html', active='my-leagues', discovered=discovered,
                               username=username, season=season, error=None)
    return render_template('onboard.html', active='my-leagues', discovered=None,
                           username='', season=default_season, error=None,
                           message=request.args.get('message', ''))


@app.route('/my/onboard/import', methods=['POST'])
@login_required
def onboard_import():
    selected = request.form.getlist('selected')
    if not selected:
        return redirect(url_for('onboard'))

    with SessionLocal() as session:
        for platform_league_id in selected:
            league = (
                session.query(DbLeague)
                .filter_by(platform='sleeper', platform_league_id=platform_league_id)
                .one_or_none()
            )
            if league is None:
                league = DbLeague(
                    slug=f'sleeper-{platform_league_id}',
                    platform='sleeper',
                    platform_league_id=platform_league_id,
                    name=request.form.get(f'name_{platform_league_id}', platform_league_id),
                    season=request.form.get(f'season_{platform_league_id}') or None,
                    total_teams=request.form.get(f'teams_{platform_league_id}', type=int),
                )
                session.add(league)
                session.flush()
            link = (
                session.query(UserLeague)
                .filter_by(user_id=current_user.id, league_id=league.id)
                .one_or_none()
            )
            if link is None:
                session.add(UserLeague(user_id=current_user.id, league_id=league.id))
        session.commit()

    for platform_league_id in selected:
        queue_league_sync(platform_league_id, 'sleeper')
    return redirect(url_for('my_leagues', message=f'Imported {len(selected)} league(s); sync running in background.'))


@app.route('/my/onboard/espn', methods=['POST'])
@login_required
def onboard_espn():
    league_id = request.form.get('league_id', '').strip()
    season_raw = request.form.get('season', '').strip() or str(datetime.now().year)
    espn_s2 = request.form.get('espn_s2', '').strip() or None
    swid = request.form.get('swid', '').strip() or None
    if not league_id.isdigit() or not season_raw.isdigit():
        return redirect(url_for('onboard', message='ESPN league ID and season must be numbers.'))
    season = int(season_raw)

    # Sync inline — it doubles as validation (bad id / private league fail here).
    try:
        summary = espn_manager.sync_league(league_id, season, espn_s2=espn_s2, swid=swid)
    except PermissionError as exc:
        return redirect(url_for('onboard', message=str(exc)))
    except LookupError as exc:
        return redirect(url_for('onboard', message=str(exc)))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return redirect(url_for('onboard', message=f'ESPN import failed: {exc}'))

    with SessionLocal() as session:
        league = session.query(DbLeague).filter_by(platform='espn', platform_league_id=league_id).one_or_none()
        if league is None:
            league = DbLeague(
                slug=f'espn-{league_id}',
                platform='espn',
                platform_league_id=league_id,
                name=summary.get('name') or f'ESPN league {league_id}',
                season=str(season),
                total_teams=summary.get('rosterCount'),
            )
            session.add(league)
            session.flush()
        link = session.query(UserLeague).filter_by(user_id=current_user.id, league_id=league.id).one_or_none()
        if link is None:
            session.add(UserLeague(user_id=current_user.id, league_id=league.id))
        if espn_s2 and swid:
            credential = (
                session.query(EspnCredential)
                .filter_by(user_id=current_user.id, platform_league_id=league_id)
                .one_or_none()
            )
            if credential is None:
                credential = EspnCredential(user_id=current_user.id, platform_league_id=league_id,
                                            espn_s2_encrypted='', swid_encrypted='')
                session.add(credential)
            credential.espn_s2_encrypted = encrypt_value(espn_s2)
            credential.swid_encrypted = encrypt_value(swid)
        session.commit()

    return redirect(url_for('my_leagues', message=f"Imported {summary.get('name') or league_id} from ESPN."))


def _league_page_ctx(league, tool: str) -> dict:
    return {
        'league_slug': league.league_id,
        'league_display_name': league.name,
        'league_platform': league.platform,
        'league_tool': tool,
        'league_overview_href': _league_href(league.platform, league.platform_league_id),
    }


@app.route('/league/<league_id>/keepers')
def league_keepers(league_id: str):
    league = _member_league(league_id)
    if league is None:
        return redirect(url_for('leagues_view'))
    if league.platform == 'yahoo':
        return redirect(url_for('keepers_board_view', league=league.league_id))

    ctx = _league_page_ctx(league, 'keepers')
    if league.format.keeper_slots <= 0:
        return render_template('league_keepers.html', active='league-keepers', per_team=[],
                               remaining_board=[], keeper_impact=[], keeper_marks={}, not_configured=True,
                               error=None, **ctx)

    state = keeper_board_state(league, include_file_prefs=False,
                               user_id=current_user.id if current_user.is_authenticated else None)
    if state['error']:
        return render_template('league_keepers.html', active='league-keepers', per_team=[],
                               remaining_board=[], keeper_impact=[], keeper_marks={}, not_configured=False,
                               error=state['error'], **ctx)

    return render_template('league_keepers.html', active='league-keepers', per_team=state['per_team'],
                           remaining_board=state['remaining_board'], keeper_impact=state['keeper_impact'],
                           keeper_count=state['keeper_count'], keeper_marks=state['include_marks'],
                           not_configured=False, error=None,
                           can_adjust=current_user.is_authenticated,
                           has_adjustments=any(row.get('userOffset') for row in state['remaining_board']),
                           message=request.args.get('message', ''), **ctx)


@app.route('/league/<league_id>/draft-analysis')
def league_draft_analysis(league_id: str):
    """Did draft slot predict finish, and which positions in round N did?
    (Phase 3 port -- runs on any registered league via its own repository.)

    Both analyses correlate against final standings, so a league only has
    something to show once it has at least one season with BOTH draft results
    and saved standings; that's an empty state, not an error."""
    league = _member_league(league_id)
    if league is None:
        return redirect(url_for('leagues_view'))

    repo = repository_for(league)
    round_number = request.args.get('round', default=1, type=int)

    slot_outcomes = draft_slot_vs_final_rank(repo=repo)
    slot_summary = summarize_draft_slot_correlation(slot_outcomes) if slot_outcomes else {}
    position_outcomes = position_in_round_vs_final_rank(round_number, repo=repo)
    position_summary = summarize_position_in_round(position_outcomes) if position_outcomes else {}

    return render_template(
        'league_draft_analysis.html', active='league-draft-analysis',
        slot_summary=slot_summary, position_summary=position_summary,
        round_number=round_number, has_data=bool(slot_outcomes or position_outcomes),
        **_league_page_ctx(league, 'draft-analysis'),
    )


@app.route('/league/<league_id>/manager-report')
def league_manager_report(league_id: str):
    """Per-manager draft performance: did each manager's actual finish beat
    what their own draft slots would predict, using this league's own
    slot-to-rank baseline (see app/manager_report.py).

    Same empty-state gate as draft-analysis (needs a season with both draft
    results and saved standings). Also see that module's identity-resolution
    caveat, surfaced on the page itself -- rows are "team-name lineages," not
    verified people, when Yahoo's rename note never linked two names."""
    league = _member_league(league_id)
    if league is None:
        return redirect(url_for('leagues_view'))

    repo = repository_for(league)
    rows = manager_report_card(repo=repo)

    return render_template(
        'league_manager_report.html', active='league-manager-report',
        rows=rows, **_league_page_ctx(league, 'manager-report'),
    )


@app.route('/league/<league_id>/draft-patterns')
def league_draft_patterns(league_id: str):
    """What this league actually drafts, and when -- from its own history.

    Distinct from /draft-analysis, which asks whether draft decisions predicted
    the final standings. This one just describes behaviour: position mix per
    round, when each position comes off the board, and the average pick for the
    Nth player at a position."""
    league = _member_league(league_id)
    if league is None:
        return redirect(url_for('leagues_view'))

    repo = repository_for(league)
    mix = position_mix_by_round(repo)
    timing = position_timing(repo)
    targets = {
        position: position_rank_pick_targets(position, top_n=8, repo=repo)
        for position in ('QB', 'RB', 'WR', 'TE')
    }
    seasons = sorted({pick['year'] for pick in resolved_picks(repo)})

    return render_template(
        'league_draft_patterns.html', active='league-draft-patterns',
        mix=mix, timing=timing, targets={k: v for k, v in targets.items() if v},
        seasons=seasons, sample=sum(entry['n'] for entry in mix.values()),
        **_league_page_ctx(league, 'draft-patterns'),
    )


@app.route('/league/<league_id>/settings', methods=['GET', 'POST'])
def league_settings(league_id: str):
    league = _member_league(league_id)
    if league is None:
        return redirect(url_for('leagues_view'))

    if request.method == 'POST':
        if not current_user.is_authenticated:
            return login_manager.unauthorized()

        def parse_rounds(raw: str) -> list:
            return [int(part) for part in raw.replace(',', ' ').split() if part.strip().isdigit()]
        save_league_rules(league, {
            'teams': request.form.get('teams', type=int) or league.format.teams,
            'keeper_slots': request.form.get('keeper_slots', type=int) or 0,
            'keeper_ineligible_rounds': parse_rounds(request.form.get('keeper_ineligible_rounds', '')),
            'keeper_slot_rounds': parse_rounds(request.form.get('keeper_slot_rounds', '')),
            'keeper_max_consecutive_seasons': request.form.get('keeper_max_consecutive_seasons', type=int) or 0,
        })
        return redirect(url_for('league_settings', league_id=league_id, message='Rules saved.'))

    snapshot = None
    if league.platform == 'sleeper':
        snapshot = load_synced_league(league.platform_league_id)
    elif league.platform == 'espn':
        snapshot = espn_manager.load_synced_league(league.platform_league_id)
    return render_template('league_settings.html', active='league-settings', league=league,
                           fmt=league.format, snapshot=snapshot,
                           message=request.args.get('message', ''),
                           **_league_page_ctx(league, 'settings'))


@app.route('/leagues')
def leagues_view():
    """Only the current user's leagues. This used to list every league in
    leagues.json regardless of who was looking."""
    default_league = _current_default_league()
    default_id = default_league.league_id if default_league is not None else None
    providers: dict = {}
    for league in followed_leagues(current_user.id):
        providers.setdefault(league.platform, []).append({
            'leagueId': league.league_id,
            'name': league.name,
            'season': league.season,
            'teams': league.format.teams,
            'isDefault': league.league_id == default_id,
            'href': _league_href(league.platform, league.platform_league_id),
        })
    provider_order = [p for p in ('yahoo', 'sleeper', 'espn') if p in providers]
    return render_template('leagues.html', active='leagues', providers=providers,
                           provider_order=provider_order)


def _structure_rosters(rosters: list, league: dict) -> None:
    """In-place display prep: starters in lineup-slot order (snapshot starters
    arrays align with the league's non-bench roster positions), bench sorted
    by position then name."""
    position_sort = {'QB': 0, 'RB': 1, 'WR': 2, 'TE': 3, 'K': 4, 'DEF': 5}
    slot_labels = [p for p in (league.get('rosterPositions') or []) if p not in ('BN', 'IR', 'TAXI')]
    for roster in rosters:
        starters = roster.get('starters') or []
        starter_ids = {p.get('playerId') for p in starters}
        bench = [p for p in roster.get('players') or [] if p.get('playerId') not in starter_ids]
        bench.sort(key=lambda p: (position_sort.get((p.get('position') or '').upper(), 9),
                                  p.get('playerName') or ''))
        roster['bench'] = bench
        roster['starterSlots'] = [
            (slot_labels[i] if i < len(slot_labels) else (p.get('position') or ''), p)
            for i, p in enumerate(starters)
        ]


@app.route('/sleeper')
def sleeper_leagues_view():
    config = load_sleeper_leagues_config()
    leagues = []
    for entry in config.get('leagues', []):
        # The local config file lists every league the CLI ever discovered;
        # a web user only sees the ones they actually follow.
        if not user_follows_platform_league(current_user.id, 'sleeper', entry['leagueId']):
            continue
        synced = load_synced_league(entry['leagueId'])
        leagues.append({
            **entry,
            'synced': synced is not None,
            'syncedAt': synced.get('syncedAt') if synced else None,
            'status': synced.get('status') if synced else None,
        })
    return render_template('sleeper_leagues.html', active='sleeper', leagues=leagues,
                            username=config.get('sleeperUsername'))


@app.route('/sleeper/<league_id>')
def sleeper_league_view(league_id: str):
    if not user_follows_platform_league(current_user.id, 'sleeper', league_id):
        return redirect(url_for('leagues_view'))
    config = load_sleeper_leagues_config()
    entry = next((l for l in config.get('leagues', []) if l['leagueId'] == league_id), None)
    league = load_synced_league(league_id)
    if league is None:
        # sleeper_league.html was renamed to league_snapshot.html when ESPN
        # landed (commit e49664a) and this branch kept the old name -- a 500 on
        # any un-synced Sleeper league, which is now where a Sleeper user's
        # `/` sends them.
        return render_template('league_snapshot.html', active='sleeper', league_id=league_id,
                                entry=entry, league=None, rosters=[], drafts=[],
                                league_display_name=(entry or {}).get('name') or league_id,
                                league_platform='sleeper', league_slug=f'sleeper-{league_id}',
                                league_tool='overview', league_overview_href=f'/sleeper/{league_id}',
                                error='Not synced yet — sync it from /my/leagues.')

    rosters = load_synced_rosters(league_id)
    rosters_sorted = sorted(rosters, key=lambda r: (-(r.get('wins') or 0), r.get('losses') or 0))
    drafts = load_synced_drafts(league_id)
    for draft in drafts:
        draft['picks'] = sorted(draft.get('picks') or [], key=lambda p: (p.get('round') or 0, p.get('pick') or 0))
    _structure_rosters(rosters_sorted, league)

    display_name = (entry or {}).get('name') or league.get('name') or league_id
    return render_template('league_snapshot.html', active='sleeper', league_id=league_id,
                            entry=entry, league=league, rosters=rosters_sorted, drafts=drafts,
                            league_display_name=display_name, league_platform='sleeper',
                            league_slug=f'sleeper-{league_id}', league_tool='overview',
                            league_overview_href=f'/sleeper/{league_id}', error=None)


@app.route('/espn/<league_id>')
def espn_league_view(league_id: str):
    if not user_follows_platform_league(current_user.id, 'espn', league_id):
        return redirect(url_for('leagues_view'))
    league = espn_manager.load_synced_league(league_id)
    if league is None:
        return render_template('league_snapshot.html', active='espn', league_id=league_id,
                                entry=None, league=None, rosters=[], drafts=[],
                                league_display_name=league_id, league_platform='espn',
                                error='Not synced yet — import this league from /my/onboard first.')

    rosters = espn_manager.load_synced_rosters(league_id)
    rosters_sorted = sorted(rosters, key=lambda r: (-(r.get('wins') or 0), r.get('losses') or 0))
    drafts = espn_manager.load_synced_drafts(league_id)
    for draft in drafts:
        draft['picks'] = sorted(draft.get('picks') or [], key=lambda p: (p.get('round') or 0, p.get('pick') or 0))
    _structure_rosters(rosters_sorted, league)

    return render_template('league_snapshot.html', active='espn', league_id=league_id,
                            entry=None, league=league, rosters=rosters_sorted, drafts=drafts,
                            league_display_name=league.get('name') or league_id,
                            league_platform='espn', league_slug=f'espn-{league_id}',
                            league_tool='overview', league_overview_href=f'/espn/{league_id}',
                            error=None)


@app.route('/draft-history')
def draft_history_years():
    repo = _default_repo()
    if repo is None:
        return _no_league_redirect()
    years = repo.draft_years()
    return render_template('draft_history_years.html', active='draft-history', years=sorted(years.keys(), reverse=True))


@app.route('/draft-history/<int:year>')
def draft_history_view(year: int):
    repo = _default_repo()
    if repo is None:
        return _no_league_redirect()
    years = repo.draft_years()
    picks = years.get(year)
    if picks is None:
        return render_template(
            'draft_history.html', active='draft-history', year=year, rounds={},
            error=f'No saved draft history for {year}.',
        )

    mode = request.args.get('mode', 'all')
    if mode == 'live':
        picks = live_draft_picks(year, years)
    elif mode == 'keepers':
        picks = keeper_slot_picks(year, years)

    rounds: dict = {}
    for p in sorted(picks, key=lambda p: (p.get('round', 0), p.get('pick', 0))):
        rounds.setdefault(p.get('round'), []).append(p)

    return render_template('draft_history.html', active='draft-history', year=year, rounds=rounds, mode=mode, error=None)


@app.route('/standings')
def standings_years():
    repo = _default_repo()
    if repo is None:
        return _no_league_redirect()
    years = repo.standings_years()
    return render_template('standings_years.html', active='standings', years=years)


@app.route('/standings/<int:year>')
def standings_view(year: int):
    repo = _default_repo()
    if repo is None:
        return _no_league_redirect()
    standings = repo.standings(year)
    if standings is None:
        return render_template('standings.html', active='standings', year=year, standings=[], error=f'No saved standings for {year}.')
    return render_template('standings.html', active='standings', year=year, standings=standings, error=None)


@app.route('/draft-order/<int:standings_year>')
def draft_order_view(standings_year: int):
    repo = _default_repo()
    if repo is None:
        return _no_league_redirect()
    standings = repo.standings(standings_year)
    if standings is None:
        return render_template(
            'draft_order.html', active='standings', standings_year=standings_year, rounds={},
            error=f'No saved standings for {standings_year}.',
        )
    round1_order = draft_order_from_standings(standings)
    rounds = snake_draft_order(round1_order, 15)
    return render_template('draft_order.html', active='standings', standings_year=standings_year, rounds=rounds, error=None)


@app.route('/draft-picks/<int:year>')
def draft_picks_view(year: int):
    repo = _default_repo()
    if repo is None:
        return _no_league_redirect()
    picks = repo.draft_picks(year)
    if picks is None:
        return render_template(
            'draft_picks.html', active='draft-history', year=year, teams={}, all_rounds=[],
            error=f'No saved pick ownership for {year}.',
        )
    all_rounds = sorted({r for rounds in picks.values() for r in rounds.keys()})
    return render_template('draft_picks.html', active='draft-history', year=year, teams=picks, all_rounds=all_rounds, error=None)


@app.route('/draft-order/<int:standings_year>/board')
def draft_order_board_view(standings_year: int):
    league = _current_default_league()
    if league is None:
        return _no_league_redirect()
    if not _is_file_backed_yahoo(league):
        # This board is built on the file-backed league format + shared keeper
        # marks; the per-league equivalent is /league/<slug>/keepers.
        return redirect(url_for('league_keepers', league_id=league.league_id))
    repo = repository_for(league)
    standings = repo.standings(standings_year)
    if standings is None:
        return render_template(
            'draft_order_board.html', active='standings', standings_year=standings_year, teams={},
            error=f'No saved standings for {standings_year}.',
        )

    rankings = repo.rankings()
    if not rankings:
        return render_template('draft_order_board.html', active='standings', standings_year=standings_year, teams={}, error=(
            'No saved rankings. Run `python3 -m app.cli refresh-yahoo-rankings` or `import-rankings-csv` first.'
        ))

    league_rosters = repo.rosters()
    if not league_rosters:
        return render_template('draft_order_board.html', active='standings', standings_year=standings_year, teams={}, error=(
            f'No saved league roster snapshot at {YAHOO_LEAGUE_ROSTERS_JSON}. '
            'Run `python3 -m app parse-rosters` first.'
        ))

    league_format = load_league_format()
    teams = league_format.teams if league_format else 12
    live_rounds = 13

    aliases = current_team_names(standings)
    round1_order = [aliases.get(name, name) for name in draft_order_from_standings(standings)]
    rounds = snake_draft_order(round1_order, live_rounds)

    include_marks, exclude_marks = load_keeper_marks()
    _, remaining_board = league_keeper_board(
        league_rosters, rankings, league_format, keeper_count=league_format.keeper_slots,
        keeper_prefs_override=include_marks, keeper_excludes_override=exclude_marks,
    )
    board_by_rank = {row.get('draftOrder'): row for row in remaining_board}

    draft_year = request.args.get('picks_year', type=int) or standings_year + 1
    origins_by_team = repo.draft_pick_origins(draft_year)

    picks_by_team: dict = {team: [] for team in round1_order}
    for round_number, order in rounds.items():
        slot_position = {team: position for position, team in enumerate(order, start=1)}
        for owning_team in round1_order:
            owners = (
                origins_by_team.get(owning_team, {}).get(round_number, [owning_team])
                if origins_by_team is not None else [owning_team]
            )
            for origin_team in owners:
                position = slot_position.get(origin_team)
                if position is None:
                    continue
                overall_pick = (round_number - 1) * teams + position
                picks_by_team[owning_team].append({
                    'round': round_number,
                    'pick': overall_pick,
                    'fromTeam': origin_team if origin_team != owning_team else None,
                    'player': board_by_rank.get(overall_pick),
                })
    for entries in picks_by_team.values():
        entries.sort(key=lambda e: e['pick'])

    return render_template(
        'draft_order_board.html', active='standings', standings_year=standings_year, teams=picks_by_team, error=None,
    )


def _simulate_mock_draft(league=None) -> dict:
    """Run the mock draft for a league and group the picks for rendering.

    league=None is the default (Yahoo) league. Keepers come from live
    keeper-board state (keeper_marks DB overrides + auto-fill), so the sim
    reflects whatever is selected right now.
    Returns {'picks', 'picks_by_round', 'picks_by_team', 'error'}."""
    from .mock_draft import current_teams_from_keeper_board, run_mock_draft

    board_state = keeper_board_state(league, include_file_prefs=league is None)
    if board_state.get('error'):
        return {'picks': [], 'picks_by_round': {}, 'picks_by_team': {}, 'error': board_state['error']}

    current_teams = current_teams_from_keeper_board(board_state['per_team'])
    picks = run_mock_draft(
        current_teams,
        repo=board_state['repo'],
        league_format=board_state['league_format'],
    )

    picks_by_round: dict = {}
    picks_by_team: dict = {}
    for pick in picks:
        picks_by_round.setdefault(pick['round'], []).append(pick)
        picks_by_team.setdefault(pick['team'], []).append(pick)
    return {'picks': picks, 'picks_by_round': picks_by_round, 'picks_by_team': picks_by_team, 'error': None}


@app.route('/mock-draft')
def mock_draft_view():
    """Empty state by default -- simulating a full draft isn't free, so it's an
    explicit action (POST /actions/run-mock-draft) rather than run-on-every-GET.
    ?ran=1 (set by that action's redirect) renders the just-computed result."""
    league = _yahoo_page_league()
    if league is None:
        return _no_league_redirect()
    if not _is_file_backed_yahoo(league):
        passthrough = {k: v for k, v in request.args.items() if k != 'league'}
        return redirect(url_for('league_mock_draft', league_id=league.league_id, **passthrough))

    if request.args.get('ran') != '1':
        return render_template(
            'mock_draft.html', active='mock-draft', picks=[], picks_by_round={}, picks_by_team={},
            error=None, ran=False, league_slug=league.league_id,
            message=request.args.get('message', ''),
        )

    try:
        result = _simulate_mock_draft()
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        result = {'picks': [], 'picks_by_round': {}, 'picks_by_team': {}, 'error': str(exc)}

    return render_template(
        'mock_draft.html', active='mock-draft', ran=True, league_slug=league.league_id,
        message=request.args.get('message', ''), **result,
    )


@app.route('/league/<league_id>/mock-draft')
def league_mock_draft(league_id: str):
    """Mock draft for any registered league (Phase 3 port). Same explicit-run
    pattern as /mock-draft: empty until ?ran=1."""
    league = _member_league(league_id)
    if league is None:
        return redirect(url_for('leagues_view'))
    if league.platform == 'yahoo':
        passthrough = {k: v for k, v in request.args.items() if k != 'league'}
        return redirect(url_for('mock_draft_view', league=league.league_id, **passthrough))

    ctx = _league_page_ctx(league, 'mock-draft')
    if request.args.get('ran') != '1':
        return render_template(
            'league_mock_draft.html', active='league-mock-draft', picks=[], picks_by_round={},
            picks_by_team={}, error=None, ran=False, **ctx)

    try:
        result = _simulate_mock_draft(league)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        result = {'picks': [], 'picks_by_round': {}, 'picks_by_team': {}, 'error': str(exc)}

    return render_template('league_mock_draft.html', active='league-mock-draft', ran=True, **result, **ctx)


@app.route('/actions/run-mock-draft', methods=['POST'])
def run_mock_draft_action():
    """On-demand trigger for the mock draft sim -- picks up whatever rankings
    and keeper predictions are on disk right now (run /actions/refresh-rankings
    first if you want fresh ADP baked in). league_slug posts back to that
    league's own page instead of the default one."""
    league_slug = request.form.get('league_slug', '').strip()
    league = _member_league(league_slug) if league_slug else _current_default_league()
    if league is None:
        return _no_league_redirect()
    if _is_file_backed_yahoo(league):
        return redirect(url_for('mock_draft_view', league=league.league_id, ran='1'))
    return redirect(url_for('league_mock_draft', league_id=league.league_id, ran='1'))


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8000, debug=True)
