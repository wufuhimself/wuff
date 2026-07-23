import csv
import io
import json
from io import BytesIO
from pathlib import Path
from typing import Optional

from flask import Flask, redirect, render_template, request, url_for

from .draft_history import keeper_slot_picks, live_draft_picks, load_draft_years
from .draft_picks import load_draft_pick_origins, load_draft_picks
from .league_context import load_league_format
from .paths import CONFIG_DIR, RAW_STANDINGS_DIR, YAHOO_LEAGUE_ROSTERS_JSON, PROCESSED_DIR
from .rankings_csv import parse_rankings_csv
from .rankings_pdf import parse_rankings_pdf
from .roster_store import load_roster, save_roster as persist_roster
from .standings import current_team_names, draft_order_from_standings, load_standings, snake_draft_order
from .strategy import (
    forecast_opponent_keepers,
    league_keeper_board,
    load_yahoo_rankings,
    roster_keeper_insight,
    save_yahoo_rankings,
)
from .token_store import get_valid_token
from .yahoo_client import fetch_yahoo_rankings, fetch_yahoo_roster_players
from .gem_finder import load_and_analyze

app = Flask(__name__, template_folder='templates', static_folder='static')

LEAGUE_RULES_FILE = CONFIG_DIR / 'league_rules.json'


def load_adp_value_analysis():
    """Load ADP value analysis from CSV."""
    adp_csv = PROCESSED_DIR / 'adp_value_analysis.csv'
    if not adp_csv.exists():
        return []

    analysis = []
    with open(adp_csv, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            analysis.append({
                'playerName': row['playerName'],
                'position': row['position'],
                'rank': int(row['rank']),
                'adp': float(row['adp']),
                'delta': float(row['delta']),
                'value': row['value'],
            })
    return analysis


def load_dashboard_state():
    roster = load_roster()
    rankings = load_yahoo_rankings()
    league_format = load_league_format()
    keeper_insight = roster_keeper_insight(roster, rankings, league_format=league_format) if roster and rankings else []

    adp_analysis = load_adp_value_analysis()

    return {
        'roster': roster,
        'rankings_count': len(rankings),
        'keeper_insight': keeper_insight,
        'adp_analysis': adp_analysis,
        'has_token': get_valid_token() is not None,
    }


@app.route('/')
def index():
    state = load_dashboard_state()

    # Load draft board data for post-keeper tables
    try:
        league_rosters = json.loads(YAHOO_LEAGUE_ROSTERS_JSON.read_text())
    except FileNotFoundError:
        league_rosters = []

    rankings = load_yahoo_rankings()
    if rankings:
        league_format = load_league_format()
        per_team, remaining_board = league_keeper_board(league_rosters, rankings, league_format, keeper_count=2)
        remaining_board = remaining_board[:100]

        adp_map = load_adp_map()
        for row in remaining_board:
            enrich_with_adp([row], adp_map)

        teams = league_format.teams if league_format else 12
        for row in remaining_board:
            row['isMyPick'] = False

        board_by_rank = sorted(remaining_board, key=lambda x: x.get('ranking') or 999)
        board_by_adp = sorted(remaining_board, key=lambda x: x.get('adp') or 999)

        state['board_by_rank'] = board_by_rank
        state['board_by_adp'] = board_by_adp

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
    from .adp_manager import normalize_player_name
    adp_csv = PROCESSED_DIR / 'adp_value_analysis.csv'
    if not adp_csv.exists():
        return {}

    adp_map = {}
    with open(adp_csv, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = normalize_player_name(row['playerName'])
            adp_map[name] = float(row['adp'])
    return adp_map


def enrich_with_adp(player_list, adp_map):
    """Add ADP value to player objects."""
    from .adp_manager import normalize_player_name
    for player in player_list:
        player_name = normalize_player_name(player.get('playerName', ''))
        player['adp'] = adp_map.get(player_name)


def calculate_keeper_impact(keeper_forecasts, rankings):
    """Calculate how many elite players at each position are locked up as keepers.

    Shows impact on draft board by counting HIGH confidence keepers per position.
    """
    import re

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


def forecast_keeper_decisions(per_team, adp_map, league_rosters=None):
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
        for pos in eligible_by_position:
            eligible_by_position[pos].sort(key=lambda x: x['rank'])

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
            confidence = 'low'
            reasoning = 'Will be available in draft at this tier'

            # Check if in elite tier
            is_elite = False
            if position == 'TE' and pos_rank_num and pos_rank_num <= 5:
                confidence = 'high'
                reasoning = f'TE{pos_rank_num} - top 5 scarce, keep'
                is_elite = True
            elif position == 'RB' and pos_rank_num and pos_rank_num <= 16:
                confidence = 'high'
                reasoning = f'RB{pos_rank_num} - top 16, premium keeper'
                is_elite = True
            elif position == 'WR' and pos_rank_num and pos_rank_num <= 20:
                confidence = 'high'
                reasoning = f'WR{pos_rank_num} - top 20, keep for value'
                is_elite = True
            elif position == 'QB' and pos_rank_num and pos_rank_num <= 15:
                confidence = 'high'
                reasoning = f'QB{pos_rank_num} - top 15, reasonable keeper'
                is_elite = True

            # If not elite, check if best available at position for this team
            if not is_elite and position in eligible_by_position:
                eligible_at_pos = eligible_by_position[position]
                is_best_at_pos = eligible_at_pos and eligible_at_pos[0]['name'].lower().strip() == keeper.get('playerName', '').lower().strip()

                if is_best_at_pos:
                    next_best_rank = eligible_at_pos[1]['rank'] if len(eligible_at_pos) > 1 else None
                    drop_off = (next_best_rank or 999) - (rank or 999) if next_best_rank and rank else 0

                    if len(eligible_at_pos) <= 2:
                        # Only 1-2 eligible = forced keeper
                        confidence = 'high'
                        if drop_off > 20:
                            reasoning = f'{position}{pos_rank_num or "?"} - forced keeper (huge drop-off)'
                        else:
                            reasoning = f'{position}{pos_rank_num or "?"} - forced keeper (only {len(eligible_at_pos)} eligible)'
                    elif drop_off > 25:
                        # Big drop-off (25+ ranks) to next option = forced even with more alternatives
                        confidence = 'high'
                        reasoning = f'{position}{pos_rank_num or "?"} - forced keeper (major gap to next option)'
                    elif drop_off > 10:
                        # Moderate gap = likely to keep
                        confidence = 'medium'
                        reasoning = f'{position}{pos_rank_num or "?"} - likely keeper (clear best option)'
                    else:
                        # Small gap or tied
                        confidence = 'medium'
                        reasoning = f'{position}{pos_rank_num or "?"} - best eligible at position'

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

            # Extract positional rank
            alt_pos_rank_num = None
            if alt_pos_rank:
                match = re.search(r'(\d+)', str(alt_pos_rank))
                if match:
                    alt_pos_rank_num = int(match.group(1))

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
    try:
        league_rosters = json.loads(YAHOO_LEAGUE_ROSTERS_JSON.read_text())
    except FileNotFoundError:
        return render_template('keepers_board.html', active='keepers-board', per_team=[], remaining_board=[], error=(
            f'No saved league roster snapshot at {YAHOO_LEAGUE_ROSTERS_JSON}. '
            'Run `python3 -m app.cli scrape-league-rosters` first.'
        ))

    rankings = load_yahoo_rankings()
    if not rankings:
        return render_template('keepers_board.html', active='keepers-board', per_team=[], remaining_board=[], error=(
            'No saved rankings. Run `python3 -m app.cli refresh-yahoo-rankings` or '
            '`import-rankings-csv` first.'
        ))

    league_format = load_league_format()
    per_team, remaining_board = league_keeper_board(league_rosters, rankings, league_format, keeper_count=2)
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

    standings_years = sorted((int(p.stem) for p in RAW_STANDINGS_DIR.glob('*.json')), reverse=True) if RAW_STANDINGS_DIR.exists() else []
    round1_order = None
    origins_by_team = None
    if standings_years:
        standings = load_standings(standings_years[0])
        if standings:
            aliases = current_team_names(standings)
            round1_order = [aliases.get(name, name) for name in draft_order_from_standings(standings)]
            origins_by_team = load_draft_pick_origins(standings_years[0] + 1)

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

    # Create two sorted views of the draft board
    board_by_rank = sorted(remaining_board, key=lambda x: x.get('ranking') or 999)
    board_by_adp = sorted(remaining_board, key=lambda x: x.get('adp') or 999)

    # Forecast opponent keeper decisions based on position scarcity
    keeper_forecasts = forecast_keeper_decisions(per_team, adp_map, league_rosters=league_rosters)

    # Calculate keeper impact by position
    keeper_impact = calculate_keeper_impact(keeper_forecasts, rankings)

    return render_template(
        'keepers_board.html', active='keepers-board', per_team=per_team,
        keeper_forecasts=keeper_forecasts, keeper_impact=keeper_impact,
        keeper_insight=keeper_insight,
        my_team=my_team, team_names=team_names, error=None,
    )


@app.route('/settings')
def settings():
    state = load_dashboard_state()
    return render_template('settings.html', active='settings', **state)


@app.route('/draft-history')
def draft_history_years():
    years = load_draft_years()
    return render_template('draft_history_years.html', active='draft-history', years=sorted(years.keys(), reverse=True))


@app.route('/draft-history/<int:year>')
def draft_history_view(year: int):
    years = load_draft_years()
    picks = years.get(year)
    if picks is None:
        return render_template('draft_history.html', active='draft-history', year=year, rounds={}, error=f'No saved draft history for {year}.')

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
    from .paths import RAW_STANDINGS_DIR
    years = sorted((int(p.stem) for p in RAW_STANDINGS_DIR.glob('*.json')), reverse=True) if RAW_STANDINGS_DIR.exists() else []
    return render_template('standings_years.html', active='standings', years=years)


@app.route('/standings/<int:year>')
def standings_view(year: int):
    standings = load_standings(year)
    if standings is None:
        return render_template('standings.html', active='standings', year=year, standings=[], error=f'No saved standings for {year}.')
    return render_template('standings.html', active='standings', year=year, standings=standings, error=None)


@app.route('/draft-order/<int:standings_year>')
def draft_order_view(standings_year: int):
    standings = load_standings(standings_year)
    if standings is None:
        return render_template('draft_order.html', active='standings', standings_year=standings_year, rounds={}, error=f'No saved standings for {standings_year}.')
    round1_order = draft_order_from_standings(standings)
    rounds = snake_draft_order(round1_order, 15)
    return render_template('draft_order.html', active='standings', standings_year=standings_year, rounds=rounds, error=None)


@app.route('/draft-picks/<int:year>')
def draft_picks_view(year: int):
    picks = load_draft_picks(year)
    if picks is None:
        return render_template('draft_picks.html', active='draft-history', year=year, teams={}, all_rounds=[], error=f'No saved pick ownership for {year}.')
    all_rounds = sorted({r for rounds in picks.values() for r in rounds.keys()})
    return render_template('draft_picks.html', active='draft-history', year=year, teams=picks, all_rounds=all_rounds, error=None)


@app.route('/draft-order/<int:standings_year>/board')
def draft_order_board_view(standings_year: int):
    standings = load_standings(standings_year)
    if standings is None:
        return render_template('draft_order_board.html', active='standings', standings_year=standings_year, teams={}, error=f'No saved standings for {standings_year}.')

    rankings = load_yahoo_rankings()
    if not rankings:
        return render_template('draft_order_board.html', active='standings', standings_year=standings_year, teams={}, error=(
            'No saved rankings. Run `python3 -m app.cli refresh-yahoo-rankings` or `import-rankings-csv` first.'
        ))

    try:
        league_rosters = json.loads(YAHOO_LEAGUE_ROSTERS_JSON.read_text())
    except FileNotFoundError:
        return render_template('draft_order_board.html', active='standings', standings_year=standings_year, teams={}, error=(
            f'No saved league roster snapshot at {YAHOO_LEAGUE_ROSTERS_JSON}. '
            'Run `python3 -m app.cli scrape-league-rosters` first.'
        ))

    league_format = load_league_format()
    teams = league_format.teams if league_format else 12
    live_rounds = 13

    aliases = current_team_names(standings)
    round1_order = [aliases.get(name, name) for name in draft_order_from_standings(standings)]
    rounds = snake_draft_order(round1_order, live_rounds)

    _, remaining_board = league_keeper_board(league_rosters, rankings, league_format, keeper_count=2)
    board_by_rank = {row.get('draftOrder'): row for row in remaining_board}

    draft_year = request.args.get('picks_year', type=int) or standings_year + 1
    origins_by_team = load_draft_pick_origins(draft_year)

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


@app.route('/gems')
def gems_analysis():
    season = request.args.get('season', type=int) or 2025
    analysis = load_and_analyze(season)

    if 'error' in analysis:
        return render_template('gems.html', active='gems', error=analysis['error'])

    return render_template(
        'gems.html',
        active='gems',
        season=season,
        gems=analysis.get('gems', []),
        gems_by_pos=analysis.get('gems_by_pos', {}),
        busts=analysis.get('busts', []),
        busts_by_pos=analysis.get('busts_by_pos', {}),
        positions=analysis.get('positions', {}),
        totalPlayers=analysis.get('totalPlayers', 0),
        error=None,
    )


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8000, debug=True)
