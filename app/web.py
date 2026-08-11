import json
import os
from datetime import datetime
from typing import Optional

from flask import Flask, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from . import espn_manager, sleeper_client
from .auth import get_or_create_user, init_auth
from .crypto import encrypt_value
from .db import SessionLocal, init_db
from .draft_history import keeper_slot_picks, live_draft_picks
from .free_rankings import refresh_free_rankings
from .league_context import load_league_format
from .league_registry import default_league_id, get_league, load_leagues
from .league_service import resolve_league, save_league_rules
from .models import DbLeague, EspnCredential, KeeperMark, SyncRun, UserLeague
from .paths import CONFIG_DIR, YAHOO_LEAGUE_ROSTERS_JSON
from .repository import get_repository, repository_for
from .sleeper_manager import (
    load_sleeper_leagues_config,
    load_synced_drafts,
    load_synced_league,
    load_synced_rosters,
)
from .standings import current_team_names, draft_order_from_standings, snake_draft_order
from .strategy import _normalize_name, league_keeper_board
from .sync_scheduler import ensure_scheduler_started, queue_league_sync

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


def _default_league_platform_ids() -> tuple:
    league = get_league()
    return league.platform, league.platform_league_id


def _league_href(platform: str, platform_league_id: str) -> str:
    if platform == 'sleeper':
        return f'/sleeper/{platform_league_id}'
    if platform == 'espn':
        return f'/espn/{platform_league_id}'
    return '/'


def load_keeper_marks(platform: Optional[str] = None, platform_league_id: Optional[str] = None) -> tuple:
    """User-set keeper decisions for a league (default league when unspecified).

    Returns (include_marks, exclude_marks), each {team: [player_name, ...]}.
    'include' forces a player onto that team's keeper board; 'exclude' forbids
    the algorithm from auto-picking a player, freeing the slot for the next
    best eligible one."""
    if platform is None or platform_league_id is None:
        platform, platform_league_id = _default_league_platform_ids()
    include_marks: dict = {}
    exclude_marks: dict = {}
    with SessionLocal() as session:
        rows = (
            session.query(KeeperMark)
            .filter_by(platform=platform, platform_league_id=platform_league_id)
            .order_by(KeeperMark.created_at)
            .all()
        )
    for row in rows:
        target = exclude_marks if row.action == 'exclude' else include_marks
        target.setdefault(row.team_name, []).append(row.player_name)
    return include_marks, exclude_marks


@app.context_processor
def _inject_league_context():
    try:
        name = get_league().name
    except (KeyError, OSError):
        name = 'My league'
    return {'default_league_name': name}


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
    repo = get_repository()
    league = get_league()

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
    on-demand trigger, now on /keepers-board instead of the removed
    /settings page."""
    try:
        summary = refresh_free_rankings(scoring='ppr')
    except (RuntimeError, ValueError) as exc:
        return redirect(url_for('keepers_board_view', message=f'Rankings refresh failed: {exc}'))
    return redirect(url_for('keepers_board_view', message=(
        f"Refreshed {summary['total']} rankings ({summary['ffc']} FFC ADP, "
        f"{summary['sleeperTail']} Sleeper depth)."
    )))


def team_pick_numbers(
    team_name: str, round1_order: list, live_rounds: int, teams: int, origins_by_team: Optional[dict],
) -> set:
    """Overall pick numbers (1-indexed, live rounds only) a team actually owns, honoring saved
    traded-pick origins when available; falls back to the team's own snake slot if not."""
    rounds = snake_draft_order(round1_order, live_rounds)
    picks = set()
    for round_number, order in rounds.items():
        slot_position = {t: position for position, t in enumerate(order, start=1)}
        owners = (
            origins_by_team.get(team_name, {}).get(round_number, [team_name])
            if origins_by_team is not None else [team_name]
        )
        for origin_team in owners:
            position = slot_position.get(origin_team)
            if position is not None:
                picks.add((round_number - 1) * teams + position)
    return picks


def load_adp_map() -> dict:
    """Load ADP data as dict keyed by normalized player name."""
    from .adp_manager import load_adp_json
    return {entry['playerName']: entry['adp'] for entry in load_adp_json()}


def enrich_with_adp(player_list, adp_map):
    """Add ADP value to player objects."""
    from .adp_manager import normalize_player_name
    for player in player_list:
        player_name = normalize_player_name(player.get('playerName', ''))
        player['adp'] = adp_map.get(player_name)


def calculate_keeper_impact(keeper_forecasts, league_format=None):  # pylint: disable=unused-argument
    """Calculate how many elite players at each position are locked up as keepers.

    Shows impact on draft board by counting HIGH confidence keepers per position.

    league_format: accepted but not yet used for the tier-size math below --
    TODO: elite tier sizes assume a ~12-team/SUPERFLEX-shaped league and will
    misrepresent impact % for a differently-sized imported league (e.g. a
    Sleeper dynasty league with a different team count/starter shape). Scaling
    this correctly is a product decision (what "elite tier" means per
    position), not a mechanical fix -- revisit when a non-12-team league
    actually needs accurate impact numbers.
    """
    # Define elite tier sizes (how many top players per position matter for strategy)
    elite_tiers = {
        'TE': 5,    # Top 5 elite TEs (scarce)
        'RB': 16,   # Top 16 elite RBs
        'WR': 20,   # Top 20 elite WRs
        'QB': 15,   # Top 15 elite QBs
    }

    # Count HIGH confidence keepers by position
    high_conf_keepers = {pos: 0 for pos in elite_tiers}

    for forecast in keeper_forecasts:
        for keeper in forecast['keepers']:
            position = keeper['position']
            if position in elite_tiers and keeper['confidence'] == 'high':
                high_conf_keepers[position] += 1

    # Build impact summary
    impact = []
    for position in ['TE', 'RB', 'WR', 'QB']:
        kept = high_conf_keepers[position]
        elite_tier_size = elite_tiers[position]
        available = elite_tier_size - kept

        if elite_tier_size > 0:
            pct_kept = round((kept / elite_tier_size) * 100, 1)

            impact.append({
                'position': position,
                'kept': kept,
                'elite_tier': elite_tier_size,
                'available': available,
                'pct_kept': pct_kept,
            })

    return sorted(impact, key=lambda x: x['pct_kept'], reverse=True)


def _elite_tier_confidence(position, pos_rank_num):
    """High-confidence keep if this player is in the scarce elite tier at their position."""
    if position == 'TE' and pos_rank_num and pos_rank_num <= 5:
        return 'high', f'TE{pos_rank_num} - top 5 scarce, keep'
    if position == 'RB' and pos_rank_num and pos_rank_num <= 16:
        return 'high', f'RB{pos_rank_num} - top 16, premium keeper'
    if position == 'WR' and pos_rank_num and pos_rank_num <= 20:
        return 'high', f'WR{pos_rank_num} - top 20, keep for value'
    if position == 'QB' and pos_rank_num and pos_rank_num <= 15:
        return 'high', f'QB{pos_rank_num} - top 15, reasonable keeper'
    return None


def _best_available_confidence(keeper, position, rank, pos_rank_num, eligible_by_position):
    """Confidence based on being the best eligible option at a scarce position."""
    eligible_at_pos = eligible_by_position.get(position)
    if not eligible_at_pos:
        return None
    is_best_at_pos = eligible_at_pos[0]['name'].lower().strip() == keeper.get('playerName', '').lower().strip()
    if not is_best_at_pos:
        return None

    next_best_rank = eligible_at_pos[1]['rank'] if len(eligible_at_pos) > 1 else None
    drop_off = (next_best_rank or 999) - (rank or 999) if next_best_rank and rank else 0
    pos_label = f'{position}{pos_rank_num or "?"}'

    if len(eligible_at_pos) <= 2:
        # Only 1-2 eligible = forced keeper
        reason = 'huge drop-off' if drop_off > 20 else f'only {len(eligible_at_pos)} eligible'
        return 'high', f'{pos_label} - forced keeper ({reason})'
    if drop_off > 25:
        # Big drop-off (25+ ranks) to next option = forced even with more alternatives
        return 'high', f'{pos_label} - forced keeper (major gap to next option)'
    if drop_off > 10:
        # Moderate gap = likely to keep
        return 'medium', f'{pos_label} - likely keeper (clear best option)'
    # Small gap or tied
    return 'medium', f'{pos_label} - best eligible at position'


def forecast_keeper_decisions(per_team, adp_map):
    """Forecast which keepers each team will likely keep based on position scarcity.

    Position scarcity is the key driver of keeper value - elite players at
    scarce positions (TE, RB) are worth keeping; everyone else is likely available
    in the draft at their positional tier.

    If per_team includes alternates, also shows whether teams are "forced" to keep a player
    (limited eligible keeper options) vs. "chosen" (good selection available).
    """
    import re

    forecasts = []
    for team_entry in per_team:
        team_name = team_entry['team']
        chosen = team_entry.get('chosen', [])
        alternates = team_entry.get('alternates', [])

        # Group eligible keepers by position (chosen + alternates)
        eligible_by_position = {}
        for player in chosen + alternates:
            pos = player.get('position', 'UNK').upper()
            if pos not in eligible_by_position:
                eligible_by_position[pos] = []
            eligible_by_position[pos].append({
                'name': player.get('playerName'),
                'rank': player.get('ranking') or 999,
            })

        # Sort by rank within each position to find best/worst options
        for players in eligible_by_position.values():
            players.sort(key=lambda x: x['rank'])

        forecast_keepers = []
        for keeper in chosen:
            rank = keeper.get('ranking')
            position = keeper.get('position', '').upper()
            pos_rank = keeper.get('positionRank', '')

            # Extract positional rank (e.g., "TE2" -> 2)
            pos_rank_num = None
            if pos_rank:
                match = re.search(r'(\d+)', str(pos_rank))
                if match:
                    pos_rank_num = int(match.group(1))

            # Keeper decision: elite tier OR best available at position for this team
            confidence, reasoning = _elite_tier_confidence(position, pos_rank_num) or (None, None)
            if confidence is None:
                confidence, reasoning = _best_available_confidence(
                    keeper, position, rank, pos_rank_num, eligible_by_position,
                ) or ('low', 'Will be available in draft at this tier')

            # Look up ADP for this keeper
            from .adp_manager import normalize_player_name
            normalized_name = normalize_player_name(keeper.get('playerName', ''))
            adp = adp_map.get(normalized_name) if adp_map else None

            forecast_keepers.append({
                'playerName': keeper.get('playerName'),
                'position': position,
                'rank': rank,
                'posRank': pos_rank,
                'confidence': confidence,
                'reasoning': reasoning,
                'adp': adp,
            })

        # Add top 2 alternates for comparison
        top_alternates = []
        for alt in alternates[:2]:
            alt_position = alt.get('position', '').upper()
            alt_rank = alt.get('ranking')
            alt_pos_rank = alt.get('positionRank', '')

            # Look up ADP
            from .adp_manager import normalize_player_name
            normalized_alt_name = normalize_player_name(alt.get('playerName', ''))
            alt_adp = adp_map.get(normalized_alt_name) if adp_map else None

            top_alternates.append({
                'playerName': alt.get('playerName'),
                'position': alt_position,
                'rank': alt_rank,
                'posRank': alt_pos_rank,
                'adp': alt_adp,
                'reasoning': 'Next option considered',
            })

        forecasts.append({
            'team': team_name,
            'keepers': forecast_keepers,
            'alternates': top_alternates,
        })

    return forecasts


def _keeper_board_state(
    league=None, *, keeper_count: Optional[int] = None, draft_years=None, include_file_prefs: bool = True,
) -> dict:
    """Compute the full keeper-board state for either the default Yahoo league
    (league=None) or a resolved League. Used by keepers_board_view(),
    league_keepers(), and keeper_mark() so the AJAX response and the full-page
    render can never drift out of sync.

    Returns {'error': str} if rosters/rankings aren't available yet, otherwise
    {'repo', 'league_format', 'per_team', 'remaining_board', 'keeper_forecasts',
    'keeper_impact', 'include_marks', 'exclude_marks', 'error': None}.
    """
    if league is None:
        repo = get_repository()
        league_format = load_league_format()
        platform, platform_league_id = _default_league_platform_ids()
    else:
        repo = repository_for(league)
        league_format = league.format
        platform, platform_league_id = league.platform, league.platform_league_id

    league_rosters = repo.rosters()
    if not league_rosters:
        return {'error': (
            f'No saved league roster snapshot at {YAHOO_LEAGUE_ROSTERS_JSON}. '
            'Run `python3 -m app parse-rosters` first.'
        ) if league is None else 'No synced rosters yet -- sync the league first.'}

    rankings = repo.rankings()
    if not rankings:
        return {'error': (
            'No saved rankings. Run `python3 -m app.cli refresh-yahoo-rankings` or '
            '`import-rankings-csv` first.'
        ) if league is None else 'No synced rankings yet -- sync the league first.'}

    include_marks, exclude_marks = load_keeper_marks(platform, platform_league_id)
    resolved_keeper_count = keeper_count if keeper_count is not None else league_format.keeper_slots
    resolved_draft_years = draft_years if draft_years is not None else (repo.draft_years() if league is not None else None)
    per_team, remaining_board = league_keeper_board(
        league_rosters, rankings, league_format, keeper_count=resolved_keeper_count,
        keeper_prefs_override=include_marks, keeper_excludes_override=exclude_marks,
        draft_years=resolved_draft_years, include_file_prefs=include_file_prefs,
    )
    remaining_board = remaining_board[:100]

    adp_map = load_adp_map()
    # Name-only lookup -- doesn't cover DEF (rosters use team nickname, e.g.
    # "Broncos"; rankings use "Denver Defense") the way roster_keeper_insight's
    # ranking already does via _find_ranking_for_player's team-code fallback.
    # Only use this to fill in a ranking that isn't already set, never to
    # clobber one insight already resolved correctly.
    rank_map = {r.get('playerName', '').lower(): r.get('ranking') for r in rankings}
    for team_entry in per_team:
        for chosen in team_entry.get('chosen', []):
            pos_rank = chosen.get('positionRank')
            if pos_rank:
                chosen['posRank'] = f"{chosen.get('position', 'UNK')}{pos_rank}"
            enrich_with_adp([chosen], adp_map)
            if chosen.get('ranking') is None:
                chosen['ranking'] = rank_map.get(chosen.get('playerName', '').lower())
        for alternate in team_entry.get('alternates', []):
            enrich_with_adp([alternate], adp_map)
            if alternate.get('ranking') is None:
                alternate['ranking'] = rank_map.get(alternate.get('playerName', '').lower())
    for row in remaining_board:
        enrich_with_adp([row], adp_map)

    keeper_forecasts = forecast_keeper_decisions(per_team, adp_map)
    keeper_impact = calculate_keeper_impact(keeper_forecasts, league_format=league_format)

    # Fixed candidate pool per team for the clickable card UI: EVERY keeper-
    # eligible player, drawn from eligiblePool (unaffected by include/exclude
    # state -- see league_keeper_board) so the same cards stay in place and in
    # the same order no matter which ones are currently toggled kept. Shows
    # the whole roster, not just a top-N slice -- a manager might have a real
    # reason to keep someone the ranking-driven algorithm wouldn't surface as
    # a top pick, and that shouldn't be impossible to select just because
    # they're not near the top of the board. Only `chosen` membership (which
    # DOES reflect excludes) decides each card's kept state.
    for team_entry in per_team:
        chosen_ids = {c.get('playerId') for c in team_entry.get('chosen', [])}
        pool = team_entry.get('eligiblePool', [])
        for player in pool:
            enrich_with_adp([player], adp_map)
            if player.get('ranking') is None:
                player['ranking'] = rank_map.get(player.get('playerName', '').lower())
        team_entry['candidates'] = [
            {
                **player,
                'kept': player.get('playerId') in chosen_ids or player.get('keeperLocked') is True,
                'locked': player.get('keeperLocked') is True,
            }
            for player in pool
        ]

    return {
        'repo': repo,
        'league_format': league_format,
        'keeper_count': resolved_keeper_count,
        'per_team': per_team,
        'remaining_board': remaining_board,
        'keeper_forecasts': keeper_forecasts,
        'keeper_impact': keeper_impact,
        'include_marks': include_marks,
        'exclude_marks': exclude_marks,
        'error': None,
    }


@app.route('/keepers-board')
def keepers_board_view():
    state = _keeper_board_state()
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
        remaining_board=remaining_board, keeper_count=state['keeper_count'],
        keeper_forecasts=state['keeper_forecasts'], keeper_impact=state['keeper_impact'],
        my_team=my_team, team_names=team_names, error=None,
        keeper_marks=include_marks, message=request.args.get('message', ''),
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

    league = None
    if league_slug:
        league = resolve_league(league_slug)
        if league is None:
            return {'error': 'Unknown league.'}, 404

    if not team or not player:
        return {'error': 'Missing team or player.'}, 400

    platform, platform_league_id = (
        (league.platform, league.platform_league_id) if league is not None else _default_league_platform_ids()
    )
    include_file_prefs = league is None

    state_before = _keeper_board_state(league, include_file_prefs=include_file_prefs)
    if state_before['error']:
        return {'error': state_before['error']}, 409

    team_entry = next((t for t in state_before['per_team'] if t['team'] == team), None)
    was_auto_chosen = bool(team_entry) and any(
        _normalize_name(c.get('playerName', '')) == _normalize_name(player) for c in team_entry['chosen']
    )
    chosen_count = len(team_entry['chosen']) if team_entry else 0

    if checked and not was_auto_chosen and chosen_count >= state_before['keeper_count']:
        # Team's already at its keeper cap -- silently no-op rather than
        # reject with an error; re-render exactly what's already there so the
        # click just does nothing instead of surfacing a warning.
        return {
            'impactHtml': render_template('_partials/keeper_impact.html', keeper_impact=state_before['keeper_impact']),
            'boardRowsHtml': render_template('_partials/draft_board_rows.html', remaining_board=state_before['remaining_board']),
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
                if _normalize_name(other.get('playerName', '')) == _normalize_name(player):
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

    state_after = _keeper_board_state(league, include_file_prefs=include_file_prefs)
    if state_after['error']:
        return {'error': state_after['error']}, 409

    return {
        'impactHtml': render_template('_partials/keeper_impact.html', keeper_impact=state_after['keeper_impact']),
        'boardRowsHtml': render_template('_partials/draft_board_rows.html', remaining_board=state_after['remaining_board']),
        'candidateCardsHtml': render_template(
            '_partials/keeper_candidate_cards.html', per_team=state_after['per_team'],
            league_slug=league_slug, keeper_count=state_after['keeper_count'],
        ),
    }


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('my_leagues'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        if '@' not in email:
            return render_template('login.html', active='login', error='Enter a valid email address.')
        user = get_or_create_user(email)
        login_user(user, remember=True)
        return redirect(url_for('my_leagues'))
    return render_template('login.html', active='login', error=None)


@app.route('/logout', methods=['POST'])
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/my/leagues')
@login_required
def my_leagues():
    with SessionLocal() as session:
        rows = (
            session.query(DbLeague)
            .join(UserLeague, UserLeague.league_id == DbLeague.id)
            .filter(UserLeague.user_id == current_user.id)
            .order_by(DbLeague.name)
            .all()
        )
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
                'platform': row.platform,
                'platformLeagueId': row.platform_league_id,
                'season': row.season,
                'teams': row.total_teams,
                'href': _league_href(row.platform, row.platform_league_id),
                'lastSyncAt': last_run.started_at.strftime('%Y-%m-%d %H:%M UTC') if last_run else None,
                'lastSyncStatus': last_run.status if last_run else None,
            })
    return render_template(
        'my_leagues.html', active='my-leagues', leagues=entries,
        message=request.args.get('message', ''),
    )


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
    league = resolve_league(league_id)
    if league is None:
        return redirect(url_for('leagues_view'))
    if league.platform == 'yahoo':
        return redirect(url_for('keepers_board_view'))

    ctx = _league_page_ctx(league, 'keepers')
    if league.format.keeper_slots <= 0:
        return render_template('league_keepers.html', active='league-keepers', per_team=[],
                               remaining_board=[], keeper_impact=[], keeper_marks={}, not_configured=True,
                               error=None, **ctx)

    state = _keeper_board_state(league, include_file_prefs=False)
    if state['error']:
        return render_template('league_keepers.html', active='league-keepers', per_team=[],
                               remaining_board=[], keeper_impact=[], keeper_marks={}, not_configured=False,
                               error=state['error'], **ctx)

    return render_template('league_keepers.html', active='league-keepers', per_team=state['per_team'],
                           remaining_board=state['remaining_board'], keeper_impact=state['keeper_impact'],
                           keeper_count=state['keeper_count'], keeper_marks=state['include_marks'],
                           not_configured=False, error=None, **ctx)


@app.route('/league/<league_id>/settings', methods=['GET', 'POST'])
@login_required
def league_settings(league_id: str):
    league = resolve_league(league_id)
    if league is None:
        return redirect(url_for('leagues_view'))

    if request.method == 'POST':
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
    default_id = default_league_id()
    providers: dict = {}
    for league in load_leagues().values():
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
    config = load_sleeper_leagues_config()
    entry = next((l for l in config.get('leagues', []) if l['leagueId'] == league_id), None)
    league = load_synced_league(league_id)
    if league is None:
        return render_template('sleeper_league.html', active='sleeper', league_id=league_id,
                                entry=entry, league=None, rosters=[], drafts=[],
                                error="Not synced yet — run `python3 -m app sleeper-sync --league-id " + league_id + "`.")

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
    years = get_repository().draft_years()
    return render_template('draft_history_years.html', active='draft-history', years=sorted(years.keys(), reverse=True))


@app.route('/draft-history/<int:year>')
def draft_history_view(year: int):
    years = get_repository().draft_years()
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
    years = get_repository().standings_years()
    return render_template('standings_years.html', active='standings', years=years)


@app.route('/standings/<int:year>')
def standings_view(year: int):
    standings = get_repository().standings(year)
    if standings is None:
        return render_template('standings.html', active='standings', year=year, standings=[], error=f'No saved standings for {year}.')
    return render_template('standings.html', active='standings', year=year, standings=standings, error=None)


@app.route('/draft-order/<int:standings_year>')
def draft_order_view(standings_year: int):
    standings = get_repository().standings(standings_year)
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
    picks = get_repository().draft_picks(year)
    if picks is None:
        return render_template(
            'draft_picks.html', active='draft-history', year=year, teams={}, all_rounds=[],
            error=f'No saved pick ownership for {year}.',
        )
    all_rounds = sorted({r for rounds in picks.values() for r in rounds.keys()})
    return render_template('draft_picks.html', active='draft-history', year=year, teams=picks, all_rounds=all_rounds, error=None)


@app.route('/draft-order/<int:standings_year>/board')
def draft_order_board_view(standings_year: int):
    repo = get_repository()
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


@app.route('/mock-draft')
def mock_draft_view():
    from .mock_draft import run_mock_draft
    try:
        picks = run_mock_draft()
        picks_by_round = {}
        picks_by_team = {}
        for pick in picks:
            round_num = pick['round']
            if round_num not in picks_by_round:
                picks_by_round[round_num] = []
            picks_by_round[round_num].append(pick)

            team = pick['team']
            if team not in picks_by_team:
                picks_by_team[team] = []
            picks_by_team[team].append(pick)

        return render_template(
            'mock_draft.html', active='mock-draft', picks=picks,
            picks_by_round=picks_by_round, picks_by_team=picks_by_team, error=None,
        )
    except Exception as e:
        return render_template('mock_draft.html', active='mock-draft', picks=[], picks_by_round={}, picks_by_team={}, error=str(e))


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8000, debug=True)
