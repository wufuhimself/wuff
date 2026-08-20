import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from flask import Flask, g, redirect, render_template, request, url_for
from markupsafe import Markup
from flask_login import current_user, login_required, login_user, logout_user

from . import espn_manager, sleeper_client
from .agent_reasoning import (
    AskInProgress,
    QUESTIONS_PER_HOUR_LIMIT,
    QuestionLimitReached,
    ask,
    conversation_history,
    has_resolved_forecasts,
    questions_asked_in_last_hour,
    thread_id_for,
)
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
from .draft_history import keeper_slot_picks, live_draft_picks
from .free_rankings import manual_refresh_cooldown_remaining, refresh_free_rankings
from .domain import BRACKET_TYPES
from .franchise_store import franchise_id_for_team
from .franchise_store import get_registry as franchise_registry_for
from .keeper_service import (
    keeper_board_state,
    load_keeper_marks,
    log_team_keeper_forecast,
    set_keeper_mark,
    team_pick_numbers,
)
from .league_context import load_league_format
from .league_service import resolve_league, save_league_rules
from .mailer import send_magic_link
from .membership import (
    default_league_for_user,
    followed_league_rows,
    set_default_league,
    unfollow_league,
    user_follows,
    user_follows_platform_league,
)
from .models import DbLeague, EspnCredential, SyncRun, UserLeague
from .paths import CONFIG_DIR, YAHOO_LEAGUE_ROSTERS_JSON
from .repository import repository_for
from .sleeper_manager import load_sleeper_leagues_config, load_synced_league
from .standings import current_team_names, draft_order_from_standings, snake_draft_order
from .player_registry import normalize_name
from .strategy import league_keeper_board
from .sync_scheduler import ensure_scheduler_started, manual_sync_cooldown_remaining, queue_league_sync

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
# 'index' is here because / is the public landing page for a signed-out
# visitor (index() itself branches on current_user.is_authenticated) --
# every other route stays gated.
PUBLIC_ENDPOINTS = frozenset({'index', 'login', 'login_verify', 'logout', 'static'})


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


def _format_cooldown(remaining) -> str:
    """timedelta -> 'Xh Ym' (or 'Ym' under an hour), for the manual-sync and
    rankings-refresh cooldown messages/button labels."""
    total_minutes = max(1, int(remaining.total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    return f'{hours}h {minutes}m' if hours else f'{minutes}m'


def _rankings_cooldown_label() -> Optional[str]:
    remaining = manual_refresh_cooldown_remaining()
    return _format_cooldown(remaining) if remaining else None


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


def _league_has_drafted(league) -> bool:
    """Whether `league`'s upcoming draft has already happened, cached per request.

    Feeds the nav ordering (see base.html): pre-draft leagues lead with the
    tools that are still actionable -- keepers, mock draft -- while a drafted
    league leads with matchups/transactions, which have no rows before a draft.

    Cached in `g` because _inject_league_context runs on EVERY page render and
    this costs ~8ms uncached for a league with six seasons of draft history --
    against a 1.4ms render for a page like /standings, which would have made
    the nav ordering the single most expensive thing on it.
    """
    if league is None:
        return False
    cache = getattr(g, '_has_drafted_cache', None)
    if cache is None:
        cache = g._has_drafted_cache = {}  # pylint: disable=protected-access
    key = (league.platform, league.platform_league_id)
    if key not in cache:
        try:
            cache[key] = repository_for(league).has_drafted()
        except Exception:  # pylint: disable=broad-except
            # Nav ordering must never 500 a page. An unsynced or broken league
            # falls back to the pre-draft order, which is the safer default:
            # it surfaces the tools that work without any draft data.
            cache[key] = False
    return cache[key]


@app.context_processor
def _inject_league_context():
    league = _current_default_league()
    nav_leagues = []
    if current_user.is_authenticated:
        # Cheap on purpose -- runs on every page via context processor, so no
        # per-league sync status here (that's what /leagues itself is for).
        # slug is already on the row (no extra query) -- needed so the
        # per-row "delete league" link in the dropdown can post it.
        nav_leagues = [
            {'name': row.name, 'platform': row.platform, 'slug': row.slug,
             'href': _league_href(row.platform, row.platform_league_id)}
            for row in followed_league_rows(current_user.id)
        ]
    return {
        'default_league_name': league.name if league is not None else 'My leagues',
        # The shared pages (/standings, /draft-history, ...) now serve whichever
        # league is the caller's, so the platform tag can't be a literal 'yahoo'.
        'default_league_platform': league.platform if league is not None else '',
        # Lets the dashboard nav link to this league's /league/<slug>/... tools
        # (matchups, draft patterns, draft analysis, manager report, settings)
        # without hardcoding the Yahoo league's slug.
        'default_league_id': league.league_id if league is not None else '',
        # Hides the Keepers nav link when this league has no keeper slots
        # configured (e.g. redraft leagues, or a new league before settings
        # are filled in) -- same threshold /league/<slug>/keepers itself
        # already uses to show a "not configured" state instead of a 500.
        'default_league_keeper_slots': league.format.keeper_slots if league is not None else 0,
        # Drives the nav ordering in base.html -- pre-draft leagues lead with
        # keepers/mock draft, drafted ones with matchups/transactions.
        'default_league_has_drafted': _league_has_drafted(league),
        'nav_leagues': nav_leagues,
    }


def _league_overview_ctx(league, sync_error: Optional[str] = None, roster_slot_labels: Optional[list] = None) -> dict:
    """Shared context for the overview page, any platform.

    Was two routes/templates (dashboard.html for Yahoo, league_snapshot.html
    for Sleeper/ESPN) that had drifted into showing different information for
    no platform-specific reason -- e.g. only Yahoo's page showed next
    season's draft order, only Sleeper/ESPN's showed completed draft picks
    inline, and each built its own ad-hoc standings/roster dict shape by
    hand. Built on the typed repository methods (roster_teams(),
    standing_rows(), drafts()) from Phase 5 instead, which already normalize
    the platform differences that ARE real (see RosterTeam.starters'
    docstring -- Yahoo's paste genuinely carries no lineup data, Sleeper/ESPN
    genuinely do; the template splits starters/bench only when non-empty
    rather than faking one for Yahoo).
    """
    position_sort = {'QB': 0, 'RB': 1, 'WR': 2, 'TE': 3, 'K': 4, 'DEF': 5}
    # The platform's lineup slot order (e.g. QB/RB/RB/WR/WR/FLEX/SUPER_FLEX),
    # if the caller has one -- Sleeper/ESPN's league snapshot carries this,
    # Yahoo's parsed rosters never do. Distinct from a starter's own
    # position: a FLEX slot holding a WR should still read "FLEX", not "WR".
    slot_labels = [p for p in (roster_slot_labels or []) if p not in ('BN', 'IR', 'TAXI')]

    def _roster_display(team) -> tuple:
        """(starterSlots, bench) for one team, same shape the old
        per-platform helpers built by hand: starters paired with their lineup
        slot label when the platform reports a lineup (Sleeper/ESPN),
        otherwise every rostered player position-sorted into one flat list
        (Yahoo -- see RosterTeam.starters' docstring, it's genuinely empty
        there)."""
        if team is None:
            return [], []
        if team.starters:
            starter_ids = {p.platform_player_id for p in team.starters}
            bench = [p for p in team.players if p.platform_player_id not in starter_ids]
            bench.sort(key=lambda p: (position_sort.get((p.position or '').upper(), 9), p.name))
            starter_slots = [
                (slot_labels[i] if i < len(slot_labels) else (p.position or ''), p)
                for i, p in enumerate(team.starters)
            ]
            return starter_slots, bench
        flat = sorted(team.players, key=lambda p: (position_sort.get((p.position or '').upper(), 9), p.name))
        return [], flat

    repo = repository_for(league)
    available_years = repo.standings_years()
    standings_year = available_years[0] if available_years else None

    standings_rows: list = []
    round1_order: list = []
    if standings_year is not None:
        typed_standings = repo.standing_rows(standings_year) or []
        rosters_by_franchise = {t.franchise_id: t for t in repo.roster_teams() if t.franchise_id}
        rosters_by_name = {t.team_name: t for t in repo.roster_teams()}
        for row in sorted(typed_standings, key=lambda r: r.rank):
            team = rosters_by_franchise.get(row.franchise_id) or rosters_by_name.get(row.team_name)
            starter_slots, bench = _roster_display(team)
            standings_rows.append({
                'displayName': row.team_name,
                'wins': row.wins, 'losses': row.losses, 'ties': row.ties,
                'pointsFor': row.points_for, 'pointsAgainst': row.points_against,
                'starterSlots': starter_slots, 'bench': bench,
            })
        # Next season's draft order (worst record picks first) -- computable
        # for any league with standings, not just Yahoo's; draft_order_from_
        # standings/current_team_names only need the raw 'rank'/'team'/'note'
        # keys, which every backend's standings() dict already carries.
        raw_standings = repo.standings(standings_year)
        if raw_standings:
            aliases = current_team_names(raw_standings)
            round1_order = [aliases.get(name, name) for name in draft_order_from_standings(raw_standings)]

    completed_drafts = []
    for season, picks in sorted(repo.drafts().items(), reverse=True):
        completed_drafts.append({'season': season, 'picks': sorted(picks, key=lambda p: (p.round, p.pick or 0))})

    # When the next draft is, for leagues whose platform tells us (Sleeper
    # today). This is the state keeper decisions actually hang off -- once
    # picks exist it is too late to act -- so it leads the page rather than
    # sitting under the standings.
    upcoming_draft = repo.next_draft_schedule()
    draft_note = None
    if upcoming_draft is not None:
        days = upcoming_draft.days_until()
        if upcoming_draft.starts_at is None:
            draft_note = 'Draft not scheduled yet.'
        else:
            # Formatted here rather than in the template: this codebase has no
            # Jinja date filters and formats dates in Python everywhere else.
            when = upcoming_draft.starts_at.strftime('%A, %B %-d at %-I:%M %p UTC')
            if days is None or days < 0:
                draft_note = f'Draft was scheduled for {when}.'
            elif days == 0:
                draft_note = f'Draft is today — {when}.'
            elif days == 1:
                draft_note = f'Draft is tomorrow — {when}.'
            else:
                draft_note = f'Draft in {days} days — {when}.'

    return {
        'league': league, 'standings_year': standings_year, 'standings_rows': standings_rows,
        'round1_order': round1_order, 'completed_drafts': completed_drafts, 'error': sync_error,
        'upcoming_draft': upcoming_draft, 'draft_note': draft_note,
        # Own key rather than reusing _league_page_ctx's 'league_keeper_slots':
        # the Sleeper/ESPN routes splat BOTH contexts into render_template, so a
        # shared name is a duplicate-keyword TypeError. And it cannot be left to
        # _league_page_ctx alone -- the Yahoo '/' route does not pass that
        # context at all, so the key would be undefined (falsy) there and would
        # hide keeper links on the one league that actually has keepers.
        'overview_keeper_slots': league.format.keeper_slots,
    }


@app.route('/')
def index():
    if not current_user.is_authenticated:
        # The public landing page -- greets a stranger, explains what wuff
        # does, and embeds the same magic-link form /login uses (submits to
        # /login directly, so there is exactly one send-a-link code path).
        return render_template('welcome.html', active='welcome')

    league = _current_default_league()
    if league is None:
        return _no_league_redirect()
    if not _is_file_backed_yahoo(league):
        # Sleeper/ESPN leagues follow their own URL (/sleeper/<id>,
        # /espn/<id>) so a user with several leagues keeps a stable link per
        # league; only the un-scoped Yahoo dashboard lives at '/'.
        return redirect(_league_href(league.platform, league.platform_league_id))
    return render_template(
        'league_overview.html', active='dashboard',
        message=request.args.get('message', ''),
        **_league_overview_ctx(league),
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

    remaining = manual_refresh_cooldown_remaining()
    if remaining is not None:
        return redirect(url_for(next_view, message=(
            f"Rankings were refreshed recently — try again in {_format_cooldown(remaining)}."
        )))

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
    # Same rule as league_keepers above -- a league with no keeper slots has
    # no keeper board, and settings is where that number lives.
    if league.format.keeper_slots <= 0:
        return redirect(url_for('league_settings', league_id=league.league_id,
                                 message='This league has no keeper slots. Set them here to use the keeper board.'))

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
        rankings_cooldown=_rankings_cooldown_label(),
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

    set_keeper_mark(
        platform, platform_league_id, team, player,
        checked=checked,
        was_auto_chosen=was_auto_chosen,
        auto_chosen_names=[c['playerName'] for c in team_entry['chosen']] if team_entry else [],
        already_has_marks=already_has_marks,
        franchise_id=franchise_id_for_team(league, state_before['repo'], team),
    )

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
    """Old URL, kept working for bookmarks and the url_for('my_leagues')
    call sites below -- the actual page is /leagues now (see leagues_view),
    which merged this page and the old /leagues into one: they'd drifted
    into showing the same "your leagues" list twice under different nav
    labels, once grouped-by-platform with no actions, once flat with
    sync/default actions."""
    return redirect(url_for('leagues_view', message=request.args.get('message', '')))


@app.route('/my/leagues/default', methods=['POST'])
@login_required
def my_league_set_default():
    """Pick which league the un-scoped pages resolve to. Rejects leagues the
    user doesn't follow (see membership.set_default_league) -- this must not
    double as a way to claim access to someone else's league."""
    slug = request.form.get('slug', '').strip()
    if set_default_league(current_user.id, slug):
        return redirect(url_for('leagues_view', message='Default league updated.'))
    return redirect(url_for('leagues_view', message='Not one of your leagues.'))


@app.route('/my/leagues/delete', methods=['POST'])
@login_required
def my_league_delete():
    """'Delete league' -- actually unfollow (see membership.unfollow_league's
    docstring for why this is deliberately not a real delete). Rejects
    leagues the user doesn't follow, same guard as set_default -- this must
    not double as a way to touch someone else's membership."""
    slug = request.form.get('slug', '').strip()
    league = resolve_league(slug)
    name = league.name if league is not None else slug
    if unfollow_league(current_user.id, slug):
        return redirect(url_for('leagues_view', message=f'Removed {name} from your leagues.'))
    return redirect(url_for('leagues_view', message='Not one of your leagues.'))


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
        return redirect(url_for('leagues_view', message='Not one of your leagues.'))

    remaining = manual_sync_cooldown_remaining(followed.platform, platform_league_id)
    if remaining is not None:
        return redirect(url_for('leagues_view', message=(
            f"Synced recently — try again in {_format_cooldown(remaining)}."
        )))

    queued = queue_league_sync(platform_league_id, followed.platform)
    note = 'Sync started in background.' if queued else 'Synced.'
    return redirect(url_for('leagues_view', message=note))


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
        # Same keeper_slots gate as default_league_keeper_slots in
        # _inject_league_context, for the generic per-league nav branch.
        'league_keeper_slots': league.format.keeper_slots,
        'league_has_drafted': _league_has_drafted(league),
    }


@app.route('/league/<league_id>/keepers')
def league_keepers(league_id: str):
    league = _member_league(league_id)
    if league is None:
        return redirect(url_for('leagues_view'))
    if league.platform == 'yahoo':
        return redirect(url_for('keepers_board_view', league=league.league_id))

    if league.format.keeper_slots <= 0:
        # 0 slots means "not a keeper league" (that is literally how the
        # settings field is labelled), so this page has nothing to say. Sent
        # to settings rather than shown a keeper-flavoured empty state: league
        # settings is the one page keepers are allowed to appear on for a
        # league that keeps nobody, and it is also where the number is
        # changed if this is simply a league that has not been set up yet.
        return redirect(url_for('league_settings', league_id=league.league_id,
                                 message='This league has no keeper slots. Set them here to use the keeper board.'))

    ctx = _league_page_ctx(league, 'keepers')

    state = keeper_board_state(league, include_file_prefs=False,
                               user_id=current_user.id if current_user.is_authenticated else None)
    if state['error']:
        return render_template('league_keepers.html', active='league-keepers', per_team=[],
                               remaining_board=[], keeper_impact=[], keeper_marks={},
                               error=state['error'], **ctx)

    return render_template('league_keepers.html', active='league-keepers', per_team=state['per_team'],
                           remaining_board=state['remaining_board'], keeper_impact=state['keeper_impact'],
                           keeper_count=state['keeper_count'], keeper_marks=state['include_marks'],
                           error=None,
                           can_adjust=current_user.is_authenticated,
                           has_adjustments=any(row.get('userOffset') for row in state['remaining_board']),
                           message=request.args.get('message', ''), **ctx)


@app.route('/league/<league_id>/manager-report')
def league_manager_report(league_id: str):
    """Pulled from nav 2026-08-20 -- the table itself is real (per-manager
    draft-slot-vs-finish, see app/manager_report.py) but the page rendering
    it reads as a messy, ugly table, not a shippable feature. Rather than
    delete the underlying logic (manager_report_card(), the CLI
    `manager-report --league` command, and the identity-resolution work in
    Phase 5 step 3 all stay useful), this route just 302s away so no stale
    link/bookmark hits the bad page. Re-link + rework the template before
    end of season, not before.
    """
    league = _member_league(league_id)
    if league is None:
        return redirect(url_for('leagues_view'))
    return redirect(_league_href(league.platform, league.platform_league_id))


@app.route('/league/<league_id>/matchups')
def league_matchups(league_id: str):
    """Weekly head-to-head scores and the playoff bracket, from
    app/repository.py's typed matchups()/playoffs() (Phase 5 steps 7-8).

    First page to read either type -- both were built and gated in Phase 5
    but nothing surfaced them to a user until now. Sleeper-only in practice
    today: Yahoo/ESPN backends return [] from raw_matchups()/raw_playoffs()
    (no scoring data synced for them yet), which renders as an honest empty
    state below, not an error.

    PlayoffMatch carries franchise_id only, deliberately not a team name (see
    its docstring) -- resolved here, once, against the league's own
    FranchiseRegistry, rather than teaching the template to do a lookup.
    """
    league = _member_league(league_id)
    if league is None:
        return redirect(url_for('leagues_view'))

    repo = repository_for(league)
    matchups = sorted(repo.matchups(), key=lambda m: (m.week, m.home.team_name or ''))
    weeks = {}
    for matchup in matchups:
        weeks.setdefault(matchup.week, []).append(matchup)

    registry = franchise_registry_for(league, repo)

    def team_name(franchise_id):
        franchise = registry.franchises.get(franchise_id) if franchise_id else None
        return franchise.name if franchise else None

    playoffs = repo.playoffs()
    brackets = {}
    for bracket_type in BRACKET_TYPES:
        matches = sorted((m for m in playoffs if m.bracket == bracket_type),
                         key=lambda m: (m.round, m.match_id))
        if matches:
            brackets[bracket_type] = [
                {
                    'match': match,
                    'home_name': team_name(match.home_franchise_id),
                    'away_name': team_name(match.away_franchise_id),
                    'winner_name': team_name(match.winner_franchise_id),
                }
                for match in matches
            ]

    return render_template(
        'league_matchups.html', active='league-matchups',
        weeks=sorted(weeks.items()), brackets=brackets,
        **_league_page_ctx(league, 'matchups'),
    )


@app.route('/league/<league_id>/transactions')
def league_transactions(league_id: str):
    """Trades, waivers, and free-agent moves, from app/repository.py's typed
    transactions() (Phase 5 step 6).

    Built and gated in Phase 5 but never surfaced to a user until now --
    same gap league_matchups() closed for Matchup/PlayoffMatch. Sleeper-only
    in practice: ESPN has no transaction endpoint wrapped and Yahoo is still
    blocked on OAuth approval, so other platforms render the honest empty
    state below rather than an error.

    Team names on TransactionMove/TransactionPickMove already come resolved
    off the repository (attributed by roster_id, not name -- see domain.py),
    so no franchise lookup is needed here, unlike league_matchups().

    Grouped by date, then by team within a date (2026-08-20 fix -- the flat
    table had no date at all, despite Transaction.processed_at existing on
    the domain type since Phase 5 step 7; it was just never rendered). A
    transaction with moves on two teams (a trade) appears under both teams'
    groups on its date, each showing only that team's own moves plus the
    other side's name -- a trade is one event but "who did what" reads
    per-team, not as one shared blob. processed_at is epoch milliseconds,
    Sleeper's own clock; entries with no processed_at (seen on some waiver
    rows) fall into an "Undated" group at the end rather than being dropped
    or mis-sorted into a real day.
    """
    league = _member_league(league_id)
    if league is None:
        return redirect(url_for('leagues_view'))

    repo = repository_for(league)
    transactions = sorted(
        repo.transactions(),
        key=lambda t: (t.processed_at is None, t.processed_at or 0),
        reverse=True,
    )

    date_groups = []  # [(date_label, [(team_name, [(transaction, team_moves, other_team_names)])])]
    groups_by_label: Dict[str, Dict[str, List]] = {}
    order: List[str] = []

    for t in transactions:
        if t.processed_at is None:
            label = 'Undated'
        else:
            label = datetime.fromtimestamp(
                t.processed_at / 1000, tz=timezone.utc
            ).strftime('%A, %B %-d, %Y')
        if label not in groups_by_label:
            groups_by_label[label] = {}
            order.append(label)

        teams_in_txn = {m.team_name or 'Unknown team' for m in t.moves}
        teams_in_txn |= {pm.from_team_name or 'Unknown team' for pm in t.pick_moves}
        teams_in_txn |= {pm.to_team_name or 'Unknown team' for pm in t.pick_moves}
        if not teams_in_txn:
            teams_in_txn = {'Unknown team'}

        for team_name in teams_in_txn:
            team_moves = [m for m in t.moves if (m.team_name or 'Unknown team') == team_name]
            team_pick_moves = [
                pm for pm in t.pick_moves
                if team_name in (pm.from_team_name or 'Unknown team', pm.to_team_name or 'Unknown team')
            ]
            other_teams = sorted(teams_in_txn - {team_name})
            groups_by_label[label].setdefault(team_name, []).append(
                (t, team_moves, team_pick_moves, other_teams)
            )

    for label in order:
        teams = sorted(groups_by_label[label].items(), key=lambda kv: kv[0])
        date_groups.append((label, teams))

    return render_template(
        'league_transactions.html', active='league-transactions',
        date_groups=date_groups,
        **_league_page_ctx(league, 'transactions'),
    )


@app.route('/league/<league_id>/scouting', methods=['GET', 'POST'])
def league_scouting(league_id: str):
    """Natural-language Q&A over this league's outcome log (WS-6 LangGraph
    prototype, step 2 -- see app/agent_reasoning.py and the Obsidian plan).
    Branded "Scouting" in the UI (renamed 2026-08-19, was "Ask") -- the
    underlying ask()/AskInProgress names in agent_reasoning.py describe the
    mechanism (asking an LLM a question) and are unchanged; this is the
    product-facing name.

    One thread per (user, league) via agent_reasoning.thread_id_for -- your
    conversation with this league's agent persists across visits, and you
    can't see another user's thread even for a league you share. POST then
    redirect-to-GET (like league_settings above) so refreshing the page
    after asking a question doesn't resubmit it.
    """
    league = _member_league(league_id)
    if league is None:
        return redirect(url_for('leagues_view'))

    thread_id = thread_id_for(current_user.id, league.platform, league.platform_league_id)

    if request.method == 'POST':
        question = request.form.get('question', '').strip()
        if question:
            try:
                ask(league.platform, league.platform_league_id, question, thread_id,
                    league_id=league.league_id)
            except AskInProgress:
                return redirect(url_for('league_scouting', league_id=league_id,
                                         message='Still answering your last question — hang tight.'))
            except QuestionLimitReached as exc:
                return redirect(url_for('league_scouting', league_id=league_id, message=(
                    f"You've hit the {QUESTIONS_PER_HOUR_LIMIT}-question hourly limit — "
                    f"try again in {_format_cooldown(exc.retry_after)}."
                )))
        return redirect(url_for('league_scouting', league_id=league_id))

    recent = questions_asked_in_last_hour(thread_id)
    questions_remaining = max(0, QUESTIONS_PER_HOUR_LIMIT - len(recent))
    return render_template(
        'league_scouting.html', active='league-scouting',
        history=conversation_history(thread_id),
        message=request.args.get('message', ''),
        questions_remaining=questions_remaining,
        questions_per_hour_limit=QUESTIONS_PER_HOUR_LIMIT,
        has_results=has_resolved_forecasts(league.platform, league.platform_league_id),
        **_league_page_ctx(league, 'scouting'),
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
@login_required
def leagues_view():
    """The current user's leagues, grouped by platform, with per-league sync
    and default-league actions. Used to be two separate pages (/leagues
    grouped-by-platform read-only, /my/leagues flat with actions) reachable
    from two adjacent nav labels -- once membership scoped both to "your
    leagues only", they'd become the same page shown two ways. Merged into
    one; /my/leagues now redirects here."""
    rows = followed_league_rows(current_user.id)
    default_league = _current_default_league()
    default_slug = default_league.league_id if default_league is not None else None
    providers: dict = {}
    with SessionLocal() as session:
        for row in rows:
            last_run = (
                session.query(SyncRun)
                .filter_by(platform=row.platform, platform_league_id=row.platform_league_id)
                .order_by(SyncRun.started_at.desc())
                .first()
            )
            cooldown = manual_sync_cooldown_remaining(row.platform, row.platform_league_id)
            providers.setdefault(row.platform, []).append({
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
                'lastSyncDetail': last_run.detail if last_run else None,
                # Manual "Sync now" cooldown (3h, see sync_scheduler.py) --
                # None means the button is usable right now.
                'syncCooldown': _format_cooldown(cooldown) if cooldown else None,
            })
    provider_order = [p for p in ('yahoo', 'sleeper', 'espn') if p in providers]
    return render_template(
        'leagues.html', active='leagues', providers=providers, provider_order=provider_order,
        message=request.args.get('message', ''),
    )


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
    league = resolve_league(f'sleeper-{league_id}')
    if league is None:
        return redirect(url_for('leagues_view'))
    snapshot = load_synced_league(league_id)
    sync_error = None if snapshot is not None else Markup(
        'Not synced yet — sync it from <a href="/leagues">the leagues page</a>.'
    )
    slot_labels = (snapshot or {}).get('rosterPositions') or []
    return render_template(
        'league_overview.html', active='sleeper',
        **_league_overview_ctx(league, sync_error=sync_error, roster_slot_labels=slot_labels),
        **_league_page_ctx(league, 'overview'),
    )


@app.route('/espn/<league_id>')
def espn_league_view(league_id: str):
    if not user_follows_platform_league(current_user.id, 'espn', league_id):
        return redirect(url_for('leagues_view'))
    league = resolve_league(f'espn-{league_id}')
    if league is None:
        return redirect(url_for('leagues_view'))
    snapshot = espn_manager.load_synced_league(league_id)
    sync_error = None if snapshot is not None else Markup(
        'Not synced yet — import this league from <a href="/my/onboard">onboarding</a> first.'
    )
    slot_labels = (snapshot or {}).get('rosterPositions') or []
    return render_template(
        'league_overview.html', active='espn',
        **_league_overview_ctx(league, sync_error=sync_error, roster_slot_labels=slot_labels),
        **_league_page_ctx(league, 'overview'),
    )


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
    # A league with no keeper slots has no keeper/live split -- every pick is a
    # live draft pick. The mode links are hidden for it, but a stale bookmark
    # would otherwise land on a filter that means nothing here.
    default_league = _current_default_league()
    if default_league is not None and default_league.format.keeper_slots <= 0:
        mode = 'all'
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
