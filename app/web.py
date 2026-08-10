import csv
import io
import json
import os
from datetime import datetime
from io import BytesIO
from typing import Optional

from flask import Flask, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from . import sleeper_client
from .auth import get_or_create_user, init_auth
from .db import SessionLocal, init_db
from .draft_history import keeper_slot_picks, live_draft_picks
from .league_context import load_league_format
from .league_registry import default_league_id, get_league, load_leagues
from .models import DbLeague, KeeperMark, SyncRun, UserLeague
from .paths import CONFIG_DIR, YAHOO_LEAGUE_ROSTERS_JSON, PROCESSED_DIR
from .rankings_csv import parse_rankings_csv
from .rankings_pdf import parse_rankings_pdf
from .repository import get_repository
from .roster_store import load_roster, save_roster as persist_roster
from .sleeper_manager import (
    load_sleeper_leagues_config,
    load_synced_drafts,
    load_synced_league,
    load_synced_rosters,
)
from .standings import current_team_names, draft_order_from_standings, snake_draft_order
from .strategy import (
    league_keeper_board,
    roster_keeper_insight,
    save_yahoo_rankings,
)
from .sync_scheduler import ensure_scheduler_started, queue_league_sync
from .token_store import get_valid_token
from .yahoo_client import fetch_yahoo_rankings, fetch_yahoo_roster_players

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


def load_keeper_marks() -> dict:
    """User-marked keepers for the default league, as {team: [player, ...]}."""
    platform, platform_league_id = _default_league_platform_ids()
    marks: dict = {}
    with SessionLocal() as session:
        rows = (
            session.query(KeeperMark)
            .filter_by(platform=platform, platform_league_id=platform_league_id)
            .order_by(KeeperMark.created_at)
            .all()
        )
    for row in rows:
        marks.setdefault(row.team_name, []).append(row.player_name)
    return marks


@app.context_processor
def _inject_league_context():
    try:
        name = get_league().name
    except (KeyError, OSError):
        name = 'My league'
    return {'default_league_name': name}


def load_dashboard_state():
    roster = load_roster()
    rankings = get_repository().rankings()
    league_format = load_league_format()
    keeper_insight = roster_keeper_insight(roster, rankings, league_format=league_format) if roster and rankings else []

    return {
        'roster': roster,
        'rankings_count': len(rankings),
        'keeper_insight': keeper_insight,
        'has_token': get_valid_token() is not None,
    }


@app.route('/')
def index():
    state = load_dashboard_state()
    repo = get_repository()

    # Load draft board data for post-keeper tables
    league_rosters = repo.rosters()

    rankings = repo.rankings()
    if rankings:
        league_format = load_league_format()
        per_team, remaining_board = league_keeper_board(
            league_rosters, rankings, league_format, keeper_count=2,
            keeper_prefs_override=load_keeper_marks(),
        )
        remaining_board = remaining_board[:100]

        adp_map = load_adp_map()
        for row in remaining_board:
            enrich_with_adp([row], adp_map)

        teams = league_format.teams if league_format else 12

        # Load draft order for team dropdown
        available_years = repo.standings_years()
        team_names = []
        round1_order = []
        origins_by_team = None
        selected_team_picks = set()

        if available_years:
            standings = repo.standings(available_years[0])
            if standings:
                aliases = current_team_names(standings)
                round1_order = [aliases.get(name, name) for name in draft_order_from_standings(standings)]
                team_names = round1_order

                # Load draft picks for traded picks info (next season after standings year)
                origins_by_team = repo.draft_pick_origins(available_years[0] + 1)

        # Get selected team from query param or config default
        selected_team = request.args.get('team')
        if not selected_team and team_names:
            # Default to my team from config (try current year first, fall back to any key)
            my_team_config = {}
            if LEAGUE_RULES_FILE.exists():
                try:
                    my_team_config = json.loads(LEAGUE_RULES_FILE.read_text()).get('myTeam', {})
                except (json.JSONDecodeError, IOError):
                    pass

            # Try displayName keys in order (current year first, then 2025, then any)
            current_year = datetime.now().year
            selected_team = (
                my_team_config.get(f'displayName{current_year}')
                or my_team_config.get('displayName2025')
                or next((v for k, v in my_team_config.items() if k.startswith('displayName')), None)
                or team_names[0]
            )

        # Calculate picks for selected team
        if selected_team and round1_order:
            selected_team_picks = team_pick_numbers(selected_team, round1_order, 13, teams, origins_by_team)

        # Mark selected team picks and add draft order info
        for row in remaining_board:
            row['isMyPick'] = row.get('draftOrder') in selected_team_picks
            row['round'] = ((row.get('draftOrder', 1) - 1) // teams) + 1

        board_by_rank = sorted(remaining_board, key=lambda x: x.get('ranking') or 999)
        board_by_adp = sorted(remaining_board, key=lambda x: x.get('adp') or 999)

        # Calculate keeper forecasts to show which players are removed
        adp_map = load_adp_map()
        keeper_forecasts = forecast_keeper_decisions(per_team, adp_map)

        state['board_by_rank'] = board_by_rank
        state['board_by_adp'] = board_by_adp
        state['team_names'] = team_names
        state['selected_team'] = selected_team
        state['keeper_forecasts'] = keeper_forecasts

    return render_template('dashboard.html', message=request.args.get('message', ''), active='dashboard', **state)


@app.route('/actions/refresh-rankings', methods=['POST'])
def refresh_rankings():
    token = get_valid_token()
    if token is None:
        return redirect(url_for('index', message='No saved token found; run auth-server and token first.'))

    rankings = fetch_yahoo_rankings(token.access_token)
    save_yahoo_rankings(rankings)
    return redirect(url_for('index', message=f'Saved {len(rankings)} Yahoo rankings.'))


@app.route('/actions/import-rankings-csv', methods=['POST'])
def import_rankings_csv():
    uploaded_file = request.files.get('rankings_csv')
    if uploaded_file is None or not uploaded_file.filename:
        return redirect(url_for('index', message='Choose a CSV file to import rankings.'))

    source = request.form.get('source', '').strip() or uploaded_file.filename.rsplit('.', 1)[0]

    try:
        file_obj = io.StringIO(uploaded_file.stream.read().decode('utf-8-sig'))
        rankings = parse_rankings_csv(file_obj, default_source=source)
    except UnicodeDecodeError:
        return redirect(url_for('index', message='Rankings CSV must be UTF-8 encoded.'))
    except ValueError as exc:
        return redirect(url_for('index', message=str(exc)))

    save_yahoo_rankings(rankings)
    return redirect(url_for('index', message=f'Imported {len(rankings)} rankings from CSV.'))


@app.route('/actions/import-rankings-pdf', methods=['POST'])
def import_rankings_pdf():
    uploaded_file = request.files.get('rankings_pdf')
    if uploaded_file is None or not uploaded_file.filename:
        return redirect(url_for('index', message='Choose a PDF file to import rankings.'))

    source = request.form.get('source', '').strip() or uploaded_file.filename.rsplit('.', 1)[0]

    try:
        file_obj = BytesIO(uploaded_file.stream.read())
        rankings = parse_rankings_pdf(file_obj, default_source=source)
    except ValueError as exc:
        return redirect(url_for('index', message=str(exc)))

    save_yahoo_rankings(rankings)
    return redirect(url_for('index', message=f'Imported {len(rankings)} rankings from PDF.'))


@app.route('/actions/save-roster', methods=['POST'])
def save_roster():
    token = get_valid_token()
    if token is None:
        return redirect(url_for('index', message='No saved token found; run auth-server and token first.'))

    roster_players = fetch_yahoo_roster_players(token.access_token)
    persist_roster([player.__dict__ for player in roster_players])
    return redirect(url_for('index', message=f'Saved {len(roster_players)} roster players.'))


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


def list_keeper_exports():
    """Auto-discover keeper export CSVs in keeper_exports/ directory."""
    import re
    exports_dir = PROCESSED_DIR / 'keeper_exports'
    if not exports_dir.exists():
        return []

    versions = []
    for csv_file in sorted(exports_dir.glob('keepers_*.csv'), reverse=True):
        match = re.match(r'keepers_(\d{8})_([a-z0-9-]+)\.csv', csv_file.name, re.I)
        if match:
            date, method = match.groups()
            versions.append({
                'file': csv_file.name,
                'date': date,
                'method': method,
                'label': f"{date[4:6]}/{date[6:8]} - {method}"
            })

    return versions


def load_keeper_export(filename: str):
    """Load keeper export CSV by filename. Returns dict of team -> list of dicts."""
    exports_dir = PROCESSED_DIR / 'keeper_exports'
    csv_path = exports_dir / filename

    if not csv_path.exists():
        return {}

    keepers_by_team = {}
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            team = row.get('Team')
            if team not in keepers_by_team:
                keepers_by_team[team] = []
            keepers_by_team[team].append(row)

    return keepers_by_team


def organize_keeper_export(keeper_export_data):
    """Organize keeper export into structured format for template display.

    Returns: dict of team -> {'keeper_1': row, 'keeper_2': row, 'alternates': [rows]}
    """
    organized = {}
    for team, rows in keeper_export_data.items():
        keeper_1 = None
        keeper_2 = None
        alternates = []

        for row in rows:
            status = row.get('Status', '')
            if status == 'Keeper 1':
                keeper_1 = row
            elif status == 'Keeper 2':
                keeper_2 = row
            elif status.startswith('Alt'):
                alternates.append(row)

        organized[team] = {
            'keeper_1': keeper_1,
            'keeper_2': keeper_2,
            'alternates': alternates,
        }

    return organized


def forecast_from_keeper_export(keeper_export_data, rankings=None):
    """Convert keeper export data into forecast format matching keeper_forecasts structure.

    Args:
        keeper_export_data: dict of {team: [row1, row2, ...]} from load_keeper_export
                           Rows from keepers_YYYYMMDD_HHMM.csv with Status column (Keeper 1, Keeper 2, Alt 1, etc)
        rankings: optional list of ranking dicts to look up positionRank
    """
    import re

    # Build ranking lookup for position rank and ADP
    rank_map = {}
    adp_map = {}
    if rankings:
        for r in rankings:
            name_key = r.get('playerName', '').lower()
            rank_map[name_key] = r.get('posRank', '')
            if r.get('adp'):
                adp_map[name_key] = r.get('adp')

    forecasts = []

    for team, keeper_rows in sorted(keeper_export_data.items()):
        if not keeper_rows:
            continue

        # Separate keepers from alternates based on Status column
        keepers = []
        alternates = []
        for row in keeper_rows:
            status = row.get('Status', '').lower()
            player_name = row.get('PlayerName', '')
            position = row.get('Position', '?').upper()
            ranking = row.get('Ranking')

            # Extract position rank from ranking/posRank lookup
            name_key = player_name.lower()
            full_pos_rank = rank_map.get(name_key, '')
            pos_rank_num = ''
            if full_pos_rank:
                match = re.search(r'(\d+)', str(full_pos_rank))
                if match:
                    pos_rank_num = match.group(1)

            # Get ADP
            adp = adp_map.get(name_key)
            try:
                if adp:
                    adp = float(adp)
            except (ValueError, TypeError):
                adp = None

            player_data = {
                'playerName': player_name,
                'position': position,
                'rank': ranking,
                'posRank': pos_rank_num,
                'confidence': 'high',  # Export is authoritative
                'reasoning': '',
                'adp': adp,
            }

            if status.startswith('keeper'):
                keepers.append(player_data)
            elif status.startswith('alt'):
                alternates.append(player_data)

        forecasts.append({
            'team': team,
            'keepers': keepers,
            'alternates': alternates,
        })

    return forecasts


def calculate_keeper_impact(keeper_forecasts):
    """Calculate how many elite players at each position are locked up as keepers.

    Shows impact on draft board by counting HIGH confidence keepers per position.
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


@app.route('/keepers-board')
def keepers_board_view():
    # Get available keeper export versions
    keeper_versions = list_keeper_exports()
    selected_version = request.args.get('version') or (keeper_versions[0]['file'] if keeper_versions else None)

    # If keeper export available, load it instead of computing
    keeper_export_data = None
    if selected_version:
        keeper_export_data = load_keeper_export(selected_version)

    repo = get_repository()
    league_rosters = repo.rosters()
    if not league_rosters:
        return render_template('keepers_board.html', active='keepers-board', per_team=[], remaining_board=[],
                             keeper_versions=keeper_versions, selected_version=selected_version,
                             keeper_export_data=keeper_export_data, error=(
            f'No saved league roster snapshot at {YAHOO_LEAGUE_ROSTERS_JSON}. '
            'Run `python3 -m app parse-rosters` first.'
        ))

    rankings = repo.rankings()
    if not rankings:
        return render_template('keepers_board.html', active='keepers-board', per_team=[], remaining_board=[], error=(
            'No saved rankings. Run `python3 -m app.cli refresh-yahoo-rankings` or '
            '`import-rankings-csv` first.'
        ))

    league_format = load_league_format()
    keeper_marks = load_keeper_marks()
    per_team, remaining_board = league_keeper_board(
        league_rosters, rankings, league_format, keeper_count=2,
        keeper_prefs_override=keeper_marks,
    )

    remaining_board = remaining_board[:100]

    # Load ADP and enrich player data
    adp_map = load_adp_map()
    # Build ranking map by player name for quick lookup
    rank_map = {}
    for r in rankings:
        name_key = r.get('playerName', '').lower()
        rank_map[name_key] = r.get('ranking')

    for team_entry in per_team:
        for chosen in team_entry.get('chosen', []):
            pos_rank = chosen.get('positionRank')
            if pos_rank:
                position = chosen.get('position', 'UNK')
                chosen['posRank'] = f'{position}{pos_rank}'
            enrich_with_adp([chosen], adp_map)
            # Add ranking
            player_key = chosen.get('playerName', '').lower()
            chosen['ranking'] = rank_map.get(player_key)
        for alternate in team_entry.get('alternates', []):
            enrich_with_adp([alternate], adp_map)
            player_key = alternate.get('playerName', '').lower()
            alternate['ranking'] = rank_map.get(player_key)

    for row in remaining_board:
        enrich_with_adp([row], adp_map)

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

    # Use export-derived forecast if keeper export is loaded, otherwise compute from per_team
    organized_keeper_data = None
    if keeper_export_data:
        keeper_forecasts = forecast_from_keeper_export(keeper_export_data, rankings=rankings)
        organized_keeper_data = organize_keeper_export(keeper_export_data)
        # Enrich keeper data with ADP as fallback (if available)
        for team_data in organized_keeper_data.values():
            for player_dict in [team_data.get('keeper_1'), team_data.get('keeper_2')] + team_data.get('alternates', []):
                if player_dict:
                    player_name = player_dict.get('PlayerName', '').lower()
                    if player_name in adp_map:
                        player_dict['ADP'] = adp_map[player_name]
        # Also enrich keeper forecasts with ADP
        for forecast in keeper_forecasts:
            for keeper in forecast.get('keepers', []):
                player_name = keeper.get('playerName', '').lower()
                if player_name in adp_map:
                    keeper['adp'] = adp_map[player_name]
            for alt in forecast.get('alternates', []):
                player_name = alt.get('playerName', '').lower()
                if player_name in adp_map:
                    alt['adp'] = adp_map[player_name]
        keeper_impact = calculate_keeper_impact(keeper_forecasts)
    else:
        keeper_forecasts = forecast_keeper_decisions(per_team, adp_map)
        keeper_impact = calculate_keeper_impact(keeper_forecasts)

    return render_template(
        'keepers_board.html', active='keepers-board', per_team=per_team,
        keeper_forecasts=keeper_forecasts, keeper_impact=keeper_impact,
        my_team=my_team, team_names=team_names, error=None,
        keeper_versions=keeper_versions, selected_version=selected_version,
        keeper_export_data=organized_keeper_data, keeper_marks=keeper_marks,
    )


@app.route('/keepers-board/mark', methods=['POST'])
@login_required
def keeper_mark():
    team = request.form.get('team', '').strip()
    player = request.form.get('player', '').strip()
    action = request.form.get('action', 'mark')
    if not team or not player:
        return redirect(url_for('keepers_board_view'))

    platform, platform_league_id = _default_league_platform_ids()
    with SessionLocal() as session:
        existing = (
            session.query(KeeperMark)
            .filter_by(platform=platform, platform_league_id=platform_league_id,
                       team_name=team, player_name=player)
            .one_or_none()
        )
        if action == 'unmark' and existing is not None:
            session.delete(existing)
        elif action == 'mark' and existing is None:
            session.add(KeeperMark(platform=platform, platform_league_id=platform_league_id,
                                   team_name=team, player_name=player))
        session.commit()
    return redirect(url_for('keepers_board_view'))


@app.route('/settings')
def settings():
    state = load_dashboard_state()
    return render_template('settings.html', active='settings', **state)


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
                'href': f'/sleeper/{row.platform_league_id}' if row.platform == 'sleeper' else '/',
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
        follows = (
            session.query(UserLeague)
            .join(DbLeague, UserLeague.league_id == DbLeague.id)
            .filter(UserLeague.user_id == current_user.id,
                    DbLeague.platform_league_id == platform_league_id)
            .one_or_none()
        )
    if follows is None:
        return redirect(url_for('my_leagues', message='Not one of your leagues.'))
    queued = queue_league_sync(platform_league_id)
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
                           username='', season=default_season, error=None)


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
        queue_league_sync(platform_league_id)
    return redirect(url_for('my_leagues', message=f'Imported {len(selected)} league(s); sync running in background.'))


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
            'href': '/' if league.platform == 'yahoo' else f'/sleeper/{league.platform_league_id}',
        })
    provider_order = [p for p in ('yahoo', 'sleeper', 'espn') if p in providers]
    return render_template('leagues.html', active='leagues', providers=providers,
                           provider_order=provider_order)


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

    display_name = (entry or {}).get('name') or league.get('name') or league_id
    return render_template('sleeper_league.html', active='sleeper', league_id=league_id,
                            entry=entry, league=league, rosters=rosters_sorted, drafts=drafts,
                            league_display_name=display_name, error=None)


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

    _, remaining_board = league_keeper_board(
        league_rosters, rankings, league_format, keeper_count=2,
        keeper_prefs_override=load_keeper_marks(),
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
