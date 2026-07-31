from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

from .league_context import LeagueFormat, load_league_format, save_league_format
from .oauth_server import run_yahoo_oauth_server
from .paths import (
    LEAGUE_SETTINGS_FILE,
    REPO_ROOT,
    RANKINGS_COMBINED_FILE,
    YAHOO_LEAGUE_ROSTERS_CSV,
    YAHOO_LEAGUE_ROSTERS_JSON,
    YAHOO_RANKINGS_FILE,
    YAHOO_ROSTER_FILE,
    YAHOO_TOKEN_FILE,
    ensure_parent_dir,
)
from .rankings_csv import load_rankings_csv
from .rankings_pdf import load_rankings_pdf
from .rankings_aggregator import aggregate_rankings, fetch_rankings_from_site
from .rankings_manager import combine_and_save_all as combine_rankings_all, normalize_player_id
from .qb_historical_adjustment import apply_qb_historical_adjustment, compute_historical_qb_pick_targets, DEFAULT_TOP_N
from .ranking_adjustments import adjust_and_export as adjust_rankings
from .keeper_history import (
    save_keeper_history,
    load_keeper_history,
    get_team_keeper_strategy,
    save_manager_profiles_with_keepers,
)
from .team_mapper import (
    save_team_mapping,
    load_team_mapping,
)
from .feature_table import build_and_save_feature_table
from .fantasypros_manager import (
    fetch_and_save_rankings as fantasypros_fetch_rankings,
    fetch_and_save_projections as fantasypros_fetch_projections,
)
from .adp_manager import (
    import_and_save_adp,
    load_adp_json,
    rank_vs_adp_analysis,
)
from .strategy import (
    forecast_opponent_keepers,
    league_keeper_board,
    load_yahoo_rankings,
    roster_keeper_insight,
    select_best_keepers,
    save_yahoo_rankings,
)
from .roster_store import load_roster, save_roster
from .draft_analysis import (
    draft_slot_vs_final_rank,
    summarize_draft_slot_correlation,
    position_in_round_vs_final_rank,
    summarize_position_in_round,
)
from .draft_history import load_draft_years, live_draft_picks, keeper_slot_picks
from .draft_picks import load_draft_picks
from .nfl_stats import refresh_nfl_stats
from .standings import load_standings, draft_order_from_standings, snake_draft_order
from .token_store import get_valid_token, save_token
from .roster_parser import parse_yahoo_text_rosters, format_roster_preview
from .yahoo_client import (
    YahooRosterPlayer,
    exchange_code_for_token,
    fetch_standings,
    fetch_games,
    fetch_user_leagues,
    get_roster,
    get_yahoo_auth_url,
    refresh_token,
    set_lineup,
)
from .mcp_client import (
    get_sync_leagues,
    get_sync_roster,
    get_sync_draft_rankings,
    get_sync_all_team_rosters,
    get_sync_standings_for_year,
)
from .yahoo_scraper import scrape_roster, scrape_league_rosters, scrape_standings
from .config import config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Fantasy football draft and lineup assistant for Yahoo leagues')
    subparsers = parser.add_subparsers(dest='command', required=True)

    subparsers.add_parser('auth', help='Show the Yahoo OAuth authorization URL')
    subparsers.add_parser('auth-server', help='Start a local HTTPS callback server and open Yahoo OAuth authorization')

    token_parser = subparsers.add_parser('token', help='Exchange authorization code for an access token and save it locally')
    token_parser.add_argument('code', help='Yahoo OAuth authorization code')

    refresh_parser = subparsers.add_parser('refresh', help='Refresh a Yahoo OAuth refresh token and save it locally')
    refresh_parser.add_argument('refresh_token', help='Yahoo OAuth refresh token')

    rankings_parser = subparsers.add_parser('rankings', help='Fetch and aggregate rankings from multiple sites')
    rankings_parser.add_argument('--keeper', action='append', help='Keeper entries to exclude from draft rankings (playerId or playerName)')

    yahoo_rankings_parser = subparsers.add_parser('yahoo-rankings', help='Fetch projected rankings from Yahoo Fantasy')
    yahoo_rankings_parser.add_argument('access_token', nargs='?', default=None, help='Yahoo OAuth access token (optional when saved locally)')
    yahoo_rankings_parser.add_argument('--keeper', action='append', help='Keeper entries to exclude from draft rankings (playerId or playerName)')

    refresh_rankings_parser = subparsers.add_parser('refresh-yahoo-rankings', help='Refresh Yahoo rankings from the API and save locally')
    refresh_rankings_parser.add_argument('--count', type=int, default=200, help='Number of players to fetch from Yahoo')

    import_rankings_parser = subparsers.add_parser('import-rankings-csv', help='Import rankings from a manually downloaded CSV file')
    import_rankings_parser.add_argument('csv_path', help='Path to the rankings CSV file')
    import_rankings_parser.add_argument('--source', default=None, help='Override the rankings source label stored with each row')

    import_rankings_pdf_parser = subparsers.add_parser('import-rankings-pdf', help='Import rankings from a PDF file')
    import_rankings_pdf_parser.add_argument('pdf_path', help='Path to the rankings PDF file')
    import_rankings_pdf_parser.add_argument('--source', default=None, help='Override the rankings source label stored with each row')

    league_parser = subparsers.add_parser('set-league-format', help='Save league format settings used for keeper analysis')
    league_parser.add_argument('--teams', type=int, required=True, help='Number of teams in the league')
    league_parser.add_argument('--qb', type=int, default=1, help='Starting QB slots per team')
    league_parser.add_argument('--rb', type=int, default=2, help='Starting RB slots per team')
    league_parser.add_argument('--wr', type=int, default=2, help='Starting WR slots per team')
    league_parser.add_argument('--te', type=int, default=1, help='Starting TE slots per team')
    league_parser.add_argument('--flex', type=int, default=0, help='Starting FLEX slots per team')
    league_parser.add_argument('--superflex', type=int, default=0, help='Starting SUPERFLEX slots per team')
    league_parser.add_argument('--defense', type=int, default=1, help='Starting DEF slots per team')
    league_parser.add_argument('--kicker', type=int, default=1, help='Starting K slots per team')

    subparsers.add_parser('show-league-format', help='Show saved league format settings')

    keeper_insight_parser = subparsers.add_parser('keeper-insight', help='Analyze your roster and keeper value using saved rankings')
    keeper_insight_parser.add_argument('--teams', type=int, default=12, help='Number of teams in the league')

    best_keepers_parser = subparsers.add_parser('best-keepers', help='Show the best keeper choices from your saved roster')
    best_keepers_parser.add_argument('--teams', type=int, default=12, help='Number of teams in the league')
    best_keepers_parser.add_argument('--count', type=int, default=2, help='Number of keeper slots to fill')

    forecast_parser = subparsers.add_parser('forecast-keepers', help='Forecast likely opponent keepers before the keeper deadline')
    forecast_parser.add_argument('--teams', type=int, default=12, help='Number of teams in the league')
    forecast_parser.add_argument('--keepers-per-team', type=int, default=2, help='Number of keepers each team can keep')
    forecast_parser.add_argument('--top', type=int, default=100, help='Number of top available players to show')

    roster_parser = subparsers.add_parser('yahoo-roster', help='Fetch and parse your Yahoo roster into player entries')
    roster_parser.add_argument('access_token', nargs='?', default=None, help='Yahoo OAuth access token (optional when saved locally)')

    save_roster_parser = subparsers.add_parser('save-roster', help='Fetch your Yahoo roster and store it locally')
    save_roster_parser.add_argument('access_token', nargs='?', default=None, help='Yahoo OAuth access token (optional when saved locally)')

    fetch_league_rosters_parser = subparsers.add_parser('fetch-league-rosters-mcp', help='Fetch all team rosters in your league via MCP and store as league snapshot')
    fetch_league_rosters_parser.add_argument('--output', default=str(YAHOO_LEAGUE_ROSTERS_JSON), help='Path to write the league roster snapshot')

    scrape_roster_parser = subparsers.add_parser('scrape-roster', help='Web scrape your Yahoo roster using Selenium and store it locally')
    scrape_roster_parser.add_argument('--headless', action='store_true', help='Run browser in headless mode (default: visible browser)')

    scrape_league_rosters_parser = subparsers.add_parser('scrape-league-rosters', help='Web scrape every team roster in the league and store a snapshot')
    scrape_league_rosters_parser.add_argument('--teams', type=int, default=12, help='Number of teams in the league')
    scrape_league_rosters_parser.add_argument('--output', default=str(YAHOO_LEAGUE_ROSTERS_JSON), help='Path to write the league roster snapshot')
    scrape_league_rosters_parser.add_argument('--headless', action='store_true', help='Run browser in headless mode (default: visible browser)')

    export_league_rosters_parser = subparsers.add_parser('export-league-rosters-csv', help='Convert a saved league roster snapshot to CSV')
    export_league_rosters_parser.add_argument('--input', default=str(YAHOO_LEAGUE_ROSTERS_JSON), help='Path to the saved league roster snapshot')
    export_league_rosters_parser.add_argument('--output', default=str(YAHOO_LEAGUE_ROSTERS_CSV), help='Path to write the CSV export')

    subparsers.add_parser('migrate-data-layout', help='Move saved files into the current data directory layout')

    subparsers.add_parser('saved-roster', help='Show the locally saved Yahoo roster from data/raw/rosters/yahoo_roster.json')

    keepers_parser = subparsers.add_parser('yahoo-keepers', help='Fetch keeper players from Yahoo roster using round metadata')
    keepers_parser.add_argument('access_token', nargs='?', default=None, help='Yahoo OAuth access token (optional when saved locally)')

    roster_raw_parser = subparsers.add_parser('roster-raw', help='Fetch raw Yahoo roster JSON response')
    roster_raw_parser.add_argument('access_token', nargs='?', default=None, help='Yahoo OAuth access token (optional when saved locally)')

    lineup_parser = subparsers.add_parser('set-lineup', help='Set your Yahoo lineup')
    lineup_parser.add_argument('access_token', nargs='?', default=None, help='Yahoo OAuth access token (optional when saved locally)')
    lineup_parser.add_argument('lineup_json', help='JSON string or path for lineup slots')

    draft_history_parser = subparsers.add_parser('draft-history', help='Show saved draft results for a season')
    draft_history_parser.add_argument('year', type=int, help='Draft year, e.g. 2025')
    draft_history_parser.add_argument('--live-only', action='store_true', help='Exclude the last-2-rounds keeper slots (show only real draft-day picks)')
    draft_history_parser.add_argument('--keepers-only', action='store_true', help='Show only the last-2-rounds keeper-slot picks for that year')
    draft_history_parser.add_argument('--round', type=int, default=None, help='Only show this round')

    draft_picks_parser = subparsers.add_parser('draft-picks', help='Show pick ownership by round for a draft year (accounts for trades)')
    draft_picks_parser.add_argument('year', type=int, help='Draft year, e.g. 2026')
    draft_picks_parser.add_argument('--team', default=None, help='Only show this team')

    standings_parser = subparsers.add_parser('standings', help='Show final standings for a season')
    standings_parser.add_argument('year', type=int, help='Season year, e.g. 2025')

    draft_order_parser = subparsers.add_parser('draft-order', help='Show the snake draft order for the season after the given standings year (worst record picks first)')
    draft_order_parser.add_argument('standings_year', type=int, help='Standings year to invert, e.g. 2025 for a 2026 draft order')
    draft_order_parser.add_argument('--rounds', type=int, default=15, help='Number of rounds to print')

    keepers_board_parser = subparsers.add_parser('keepers-board', help='Compute best-2-keepers for every team in the saved league roster snapshot, then write a post-keepers draft board CSV')
    keepers_board_parser.add_argument('--teams', type=int, default=12, help='Number of teams in the league')
    keepers_board_parser.add_argument('--count', type=int, default=2, help='Keeper slots per team')
    keepers_board_parser.add_argument('--input', default=str(YAHOO_LEAGUE_ROSTERS_JSON), help='Path to the saved league roster snapshot')
    keepers_board_parser.add_argument('--output', default='data/processed/rankings_post_keepers.csv', help='Path to write the post-keepers CSV')

    league_keys_parser = subparsers.add_parser('list-league-keys', help='Resolve league_key mapping for historical seasons (game_key per year)')
    league_keys_parser.add_argument('--seasons', nargs='+', type=int, default=[2020, 2021, 2022, 2023, 2024, 2025], help='Seasons to resolve')

    fetch_standings_parser = subparsers.add_parser('fetch-standings', help='Fetch and save standings for a season')
    fetch_standings_parser.add_argument('year', type=int, help='Season year to fetch (e.g. 2024)')
    fetch_standings_parser.add_argument('--league-key', default=None, help='Optional: league_key (game_key.l.league_id) if known')

    scrape_standings_parser = subparsers.add_parser('scrape-standings', help='Scrape and save league standings for a season')
    scrape_standings_parser.add_argument('year', type=int, help='Season year to scrape (e.g. 2024)')
    scrape_standings_parser.add_argument('--email', default=None, help='Yahoo email (from .env if not provided)')
    scrape_standings_parser.add_argument('--password', default=None, help='Yahoo password (from .env if not provided)')
    scrape_standings_parser.add_argument('--headless', action='store_true', default=True, help='Run browser in headless mode (default: True)')
    scrape_standings_parser.add_argument('--no-headless', action='store_false', dest='headless', help='Run browser visibly')

    backfill_standings_parser = subparsers.add_parser('backfill-standings', help='Backfill standings for multiple seasons via web scraping')
    backfill_standings_parser.add_argument('--start', type=int, default=2020, help='Start year (inclusive)')
    backfill_standings_parser.add_argument('--end', type=int, default=2024, help='End year (inclusive)')
    backfill_standings_parser.add_argument('--email', default=None, help='Yahoo email (from .env if not provided)')
    backfill_standings_parser.add_argument('--password', default=None, help='Yahoo password (from .env if not provided)')
    backfill_standings_parser.add_argument('--headless', action='store_true', default=True, help='Run browser in headless mode (default: True)')
    backfill_standings_parser.add_argument('--no-headless', action='store_false', dest='headless', help='Run browser visibly')

    nfl_stats_parser = subparsers.add_parser('fetch-nfl-stats', help='Fetch and save NFL player stats (weekly, seasonal, rosters)')
    nfl_stats_parser.add_argument('--seasons', nargs='+', type=int, default=None, help='Specific seasons (default: all from 2019-current)')
    nfl_stats_parser.add_argument('--start-season', type=int, default=None, help='Start season (inclusive)')
    nfl_stats_parser.add_argument('--end-season', type=int, default=None, help='End season (inclusive)')

    draft_slot_parser = subparsers.add_parser('draft-slot-outcomes', help='Analyze draft slot (round-1 pick number) vs final standings rank')
    draft_slot_parser.add_argument('--export-csv', default=None, help='Optional: export results to CSV')

    position_round_parser = subparsers.add_parser('position-round-outcomes', help='Analyze what position was drafted in a given round vs final standings rank')
    position_round_parser.add_argument('round_number', type=int, help='Which round to analyze')
    position_round_parser.add_argument('--export-csv', default=None, help='Optional: export results to CSV')

    combine_rankings_parser = subparsers.add_parser('combine-rankings', help='Merge all ranking sources (CSV/JSON) into a single normalized file')
    combine_rankings_parser.add_argument('--output', default=None, help='Output file path (default: data/raw/rankings/rankings_combined.json)')

    qb_adjust_parser = subparsers.add_parser(
        'apply-qb-adjustment',
        help="Standard 2026+ draft-forecasting adjustment: nudge the top-N QBs (by current ranking, from data/raw/rankings/yahoo_rankings.json) "
             "up to the overall pick where a QB of that rank has actually gone in this league's own draft history, then renumber the board. "
             "Also mirrors the result into rankings_combined.json (single-source) so keepers-board-export picks it up.",
    )
    qb_adjust_parser.add_argument('--top-n', type=int, default=DEFAULT_TOP_N, help=f'How many top QBs to adjust (default {DEFAULT_TOP_N})')
    qb_adjust_parser.add_argument('--years', nargs='+', type=int, default=None, help='Draft history years to compute historical targets from (default: all available)')
    qb_adjust_parser.add_argument('--no-sync-combined', action='store_true', help='Skip mirroring into rankings_combined.json')

    adjust_rankings_parser = subparsers.add_parser('adjust-rankings', help='Apply rule-based adjustments to combined rankings (e.g., QB rushing thresholds)')
    adjust_rankings_parser.add_argument('--config', default=None, help='Path to board_adjustments.json config file (default: data/config/board_adjustments.json)')

    subparsers.add_parser('extract-keeper-history', help='Extract historical keeper selections from draft history (2020-2025)')

    subparsers.add_parser('manager-keeper-history', help='Generate manager profiles with historical keeper data by comparing rosters year-to-year')

    keeper_strategy_parser = subparsers.add_parser('keeper-strategy', help='Show a team\'s historical keeper strategy')
    keeper_strategy_parser.add_argument('team', help='Team name (e.g., "Wuf")')

    subparsers.add_parser('map-teams', help='Build owner identity mapping from draft slot consistency')

    subparsers.add_parser('refresh-oauth-token', help='Refresh expired Yahoo OAuth token using refresh token from .env')

    backfill_standings_mcp_parser = subparsers.add_parser('backfill-standings-mcp', help='Fetch historical standings (2020-current) via MCP and save locally')
    backfill_standings_mcp_parser.add_argument('--start-year', type=int, default=2020, help='Start year (default: 2020)')
    backfill_standings_mcp_parser.add_argument('--end-year', type=int, default=2025, help='End year (default: 2025)')

    fantasypros_rankings_parser = subparsers.add_parser('fantasypros-rankings', help='Fetch consensus rankings from FantasyPros API (requires FANTASYPROS_API_KEY env var)')
    fantasypros_rankings_parser.add_argument('season', type=int, help='Season year (e.g. 2025)')
    fantasypros_rankings_parser.add_argument('--sport', default='NFL', help='Sport (NFL, MLB, NBA, NHL, PGA, NCAAF; default: NFL)')
    fantasypros_rankings_parser.add_argument('--week', type=int, default=0, help='Week number (0 for preseason; default: 0)')

    fantasypros_projections_parser = subparsers.add_parser('fantasypros-projections', help='Fetch player projections from FantasyPros API (requires FANTASYPROS_API_KEY env var)')
    fantasypros_projections_parser.add_argument('season', type=int, help='Season year (e.g. 2025)')
    fantasypros_projections_parser.add_argument('--sport', default='NFL', help='Sport (NFL only for now; default: NFL)')
    fantasypros_projections_parser.add_argument('--positions', default='QB,RB,WR,TE,K,DEF', help='Colon-delimited positions to fetch (default: QB,RB,WR,TE,K,DEF)')
    fantasypros_projections_parser.add_argument('--week', type=int, default=None, help='Week number (None for preseason)')

    build_features_parser = subparsers.add_parser('build-features', help='Build ML feature table joining draft history, stats, and rankings')
    build_features_parser.add_argument('--seasons', nargs='+', type=int, default=[2022, 2023, 2024, 2025], help='Seasons to include (default: 2022-2025)')
    build_features_parser.add_argument('--output', default=None, help='Output CSV path (default: data/processed/feature_table.csv)')

    import_adp_parser = subparsers.add_parser('import-adp', help='Import ADP (Average Draft Position) from CSV file')
    import_adp_parser.add_argument('csv_path', help='Path to ADP CSV file')

    adp_analysis_parser = subparsers.add_parser('adp-value-analysis', help='Analyze rank vs ADP to find overvalued and undervalued players')
    adp_analysis_parser.add_argument('--export', default=None, help='Export results to CSV file')

    subparsers.add_parser('parse-rosters', help='Interactively parse Yahoo Fantasy rosters from pasted text')

    keepers_export_parser = subparsers.add_parser('keepers-board-export', help='Export per-team keeper recommendations and post-keepers draft board as CSVs')
    keepers_export_parser.add_argument('--teams', type=int, default=12, help='Number of teams in the league')
    keepers_export_parser.add_argument('--count', type=int, default=2, help='Keeper slots per team')
    keepers_export_parser.add_argument('--input', default=str(YAHOO_LEAGUE_ROSTERS_JSON), help='Path to the saved league roster snapshot')
    keepers_export_parser.add_argument('--output-dir', default='data/processed/keeper_exports', help='Directory to write CSV exports')

    return parser.parse_args()


def build_keepers(keeper_values: List[str] | None) -> List[Dict[str, str]]:
    if not keeper_values:
        return []

    keepers: List[Dict[str, str]] = []
    for value in keeper_values:
        if value.isdigit():
            keepers.append({'playerId': value})
        else:
            keepers.append({'playerName': value})
    return keepers


def resolve_access_token(access_token: str | None) -> str:
    if access_token:
        return access_token

    token = get_valid_token()
    if token is None:
        print('No saved token found. Run `token <code>` or `auth-server` first.', file=sys.stderr)
        sys.exit(1)

    return token.access_token


def load_saved_or_api_roster(access_token: str | None) -> tuple[List[YahooRosterPlayer], bool]:
    roster_players = load_roster()
    if roster_players:
        return roster_players, True

    # Use MCP client to fetch roster (no access_token needed)
    try:
        leagues = get_sync_leagues()
        if not leagues:
            print('No leagues found. Make sure MCP server is running on localhost:8000', file=sys.stderr)
            sys.exit(1)

        league_id = leagues[0]['id']
        roster_players = get_sync_roster(league_id)
        return roster_players, False
    except Exception as e:
        print(f'Error fetching roster from MCP server: {e}', file=sys.stderr)
        print('Make sure FastMCP server is running: python3 fastmcp_server.py', file=sys.stderr)
        sys.exit(1)


def parse_lineup_json(lineup_json: str) -> List[Dict[str, str]]:
    payload: Any
    try:
        payload = json.loads(lineup_json)
    except json.JSONDecodeError:
        file_path = Path(lineup_json)
        if file_path.exists():
            payload = json.loads(file_path.read_text(encoding='utf-8'))
        else:
            raise

    if not isinstance(payload, list):
        raise ValueError('Lineup payload must be a JSON array of objects.')

    return [
        {
            'position': item['position'],
            'playerId': item['playerId'],
        }
        for item in payload
    ]


def _display_int(value: Any) -> str:
    return str(value) if value is not None else 'N/A'


def _display_keeper_eligible(value: Any) -> str:
    if value is True:
        return 'Yes'
    if value is False:
        return 'No'
    return 'Unknown'


def _display_keeper_locked(value: Any) -> str:
    return 'Yes' if value is True else 'No'


def _clean_roster_name(value: str) -> str:
    text = value or ''
    text = text.replace('player Notes', '').replace('Player Note', '').replace('NA', '')
    text = ' '.join(text.split())
    return text.strip()


def export_league_rosters_csv(input_path: Path, output_path: Path) -> int:
    try:
        payload = json.loads(input_path.read_text())
    except FileNotFoundError:
        print(f'League roster snapshot not found: {input_path}', file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError:
        print(f'League roster snapshot is not valid JSON: {input_path}', file=sys.stderr)
        sys.exit(1)

    rows: List[Dict[str, Any]] = []
    for team in payload if isinstance(payload, list) else []:
        team_id = team.get('teamId')
        owner_name = ''
        if isinstance(team, dict):
            owner_name = str(team.get('ownerName') or team.get('managerName') or '').strip()
        if not owner_name:
            owner_name = f'Team {team_id}'
        players = team.get('players', []) if isinstance(team, dict) else []
        for player in players:
            if not isinstance(player, dict):
                continue
            rows.append(
                {
                    'ownerName': owner_name,
                    'playerId': _clean_roster_name(str(player.get('playerId', ''))),
                    'playerName': _clean_roster_name(str(player.get('playerName', ''))),
                    'position': player.get('position', 'UNK'),
                    'team': player.get('team', 'UNK'),
                    'keeperEligibleOverride': player.get('keeperEligibleOverride'),
                    'keeperLockedOverride': player.get('keeperLockedOverride'),
                    'draftRound': player.get('draftRound'),
                    'draftPick': player.get('draftPick'),
                    'draftSlot': player.get('draftSlot'),
                }
            )

    fieldnames = [
        'ownerName',
        'playerId',
        'playerName',
        'position',
        'team',
        'keeperEligibleOverride',
        'keeperLockedOverride',
        'draftRound',
        'draftPick',
        'draftSlot',
    ]
    ensure_parent_dir(output_path)
    with output_path.open('w', newline='') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return len(rows)


def migrate_data_layout() -> int:
    migration_pairs = [
        (REPO_ROOT / 'yahoo_token.json', YAHOO_TOKEN_FILE),
        (REPO_ROOT / 'league_settings.json', LEAGUE_SETTINGS_FILE),
        (REPO_ROOT / 'yahoo_rankings.json', YAHOO_RANKINGS_FILE),
        (REPO_ROOT / 'yahoo_roster.json', YAHOO_ROSTER_FILE),
        (REPO_ROOT / 'yahoo_league_rosters.json', YAHOO_LEAGUE_ROSTERS_JSON),
        (REPO_ROOT / 'yahoo_league_rosters.csv', YAHOO_LEAGUE_ROSTERS_CSV),
        (REPO_ROOT / 'data/auth/yahoo_token.json', YAHOO_TOKEN_FILE),
        (REPO_ROOT / 'data/config/league_settings.json', LEAGUE_SETTINGS_FILE),
        (REPO_ROOT / 'data/rankings/yahoo_rankings.json', YAHOO_RANKINGS_FILE),
        (REPO_ROOT / 'data/rosters/yahoo_roster.json', YAHOO_ROSTER_FILE),
        (REPO_ROOT / 'data/rosters/yahoo_league_rosters.json', YAHOO_LEAGUE_ROSTERS_JSON),
        (REPO_ROOT / 'data/rosters/yahoo_league_rosters.csv', YAHOO_LEAGUE_ROSTERS_CSV),
    ]

    moved = 0
    removed_dirs = 0
    for source, target in migration_pairs:
        if not source.exists() or source == target:
            continue
        if target.exists():
            print(f'Skipped {source} (target already exists: {target})')
            continue
        ensure_parent_dir(target)
        source.rename(target)
        moved += 1
        print(f'Moved {source} -> {target}')

    for legacy_dir in (REPO_ROOT / 'data/rankings', REPO_ROOT / 'data/rosters'):
        if not legacy_dir.exists() or not legacy_dir.is_dir():
            continue
        try:
            legacy_dir.rmdir()
            removed_dirs += 1
            print(f'Removed empty legacy directory: {legacy_dir}')
        except OSError:
            # Directory still has files, so keep it in place.
            pass

    return moved


def print_keeper_insight(insight: List[Dict[str, Any]]) -> None:
    print('Keeper insight for your roster:')
    print('-' * 80)
    for item in insight:
        print(
            f"{item['playerName']} ({item['position']}/{item['team']})"
            f"  | Ranking: {_display_int(item['ranking'])}"
            f"  | Keeper round: {_display_int(item['keeperRound'])}"
            f"  | Eligible: {_display_keeper_eligible(item.get('keeperEligible'))}"
            f"  | Locked: {_display_keeper_locked(item.get('keeperLocked'))}"
            f"  | Expected round: {_display_int(item['expectedRound'])}"
            f"  | Format round: {_display_int(item.get('formatExpectedRound'))}"
            f"  | Pos rank: {_display_int(item.get('positionRank'))}"
            f"  | Saved rounds: {_display_int(item['savedRounds'])}"
        )
        print(f"  Note: {item['note']}")
        print('-' * 80)


def print_forecast_results(likely_keepers: List[Dict[str, Any]], top_available: List[Dict[str, Any]], top_n: int = 20) -> None:
    print('Likely opponent keepers (real eligibility applied, your own team excluded):')
    print('-' * 80)
    for item in likely_keepers:
        print(f"{item.get('playerName')} ({item.get('position')})  | rank {item.get('ranking')}  | kept by {item.get('team')}")

    print()
    print(f'Top {min(top_n, len(top_available))} available after all keepers are removed:')
    print('-' * 80)
    for item in top_available[:top_n]:
        print(f"{item.get('draftOrder')}. {item.get('playerName')} ({item.get('position')})  | rank {item.get('ranking')}")


def print_best_keepers(chosen: List[Dict[str, Any]], alternates: List[Dict[str, Any]]) -> None:
    print('Best keeper choices:')
    print('-' * 80)
    for index, item in enumerate(chosen, start=1):
        prior_round = _display_int(item.get('keeperRound'))
        history_round = _display_int(item.get('leagueHistoryRound'))
        market = _display_int(item.get('marketRound') or item.get('formatExpectedRound') or item.get('expectedRound'))
        replacement = _display_int(item.get('replacementRound'))
        vor = _display_int(item.get('valueOverReplacementRounds'))
        saved = _display_int(item.get('savedRounds'))
        locked = ' locked' if item.get('keeperLocked') else ''
        print(
            f"{index}. {item['playerName']} ({item['position']}/{item['team']})"
            f"  | Last year: round {prior_round}"
            f"  | League history: round {history_round}"
            f"  | Market: round {market}"
            f"  | Replacement: round {replacement}"
            f"  | VOR: {vor}"
            f"  | Saved: {saved}"
            f"  | Eligible: {_display_keeper_eligible(item.get('keeperEligible'))}{locked}"
        )
        print(f"   {item['note']}")

    if alternates:
        print('\nBest alternates:')
        print('-' * 80)
        for index, item in enumerate(alternates[:5], start=1):
            prior_round = _display_int(item.get('keeperRound'))
            history_round = _display_int(item.get('leagueHistoryRound'))
            market = _display_int(item.get('marketRound') or item.get('formatExpectedRound') or item.get('expectedRound'))
            replacement = _display_int(item.get('replacementRound'))
            vor = _display_int(item.get('valueOverReplacementRounds'))
            saved = _display_int(item.get('savedRounds'))
            print(
                f"{index}. {item['playerName']} ({item['position']}/{item['team']})"
                f"  | Last year: round {prior_round}"
                f"  | League history: round {history_round}"
                f"  | Market: round {market}"
                f"  | Replacement: round {replacement}"
                f"  | VOR: {vor}"
                f"  | Saved: {saved}"
            )
            print(f"   {item['note']}")


def main() -> None:
    args = parse_args()

    if args.command == 'migrate-data-layout':
        moved = migrate_data_layout()
        if moved == 0:
            print('No files needed migration.')
        else:
            print(f'Migrated {moved} file(s).')
        return

    if args.command == 'auth':
        print('Open this URL in your browser:')
        print(get_yahoo_auth_url())
        return

    if args.command == 'auth-server':
        run_yahoo_oauth_server()
        return

    if args.command == 'token':
        token = exchange_code_for_token(args.code)
        save_token(token)
        print('Saved token to data/auth/yahoo_token.json')
        print(json.dumps(token.__dict__, indent=2))
        return

    if args.command == 'refresh':
        token = refresh_token(args.refresh_token)
        save_token(token)
        print('Saved refreshed token to data/auth/yahoo_token.json')
        print(json.dumps(token.__dict__, indent=2))
        return

    if args.command == 'rankings':
        sources = [
            {'url': 'https://example.com/rankings/site1', 'source': 'Site1'},
            {'url': 'https://example.com/rankings/site2', 'source': 'Site2'},
        ]
        keepers = build_keepers(args.keeper)
        all_rankings = []
        for source in sources:
            all_rankings.extend(fetch_rankings_from_site(source['url'], source['source']))
        aggregated = aggregate_rankings(all_rankings, keepers)
        print(json.dumps(aggregated[:50], indent=2))
        return

    if args.command == 'yahoo-rankings':
        keepers = build_keepers(args.keeper)
        try:
            leagues = get_sync_leagues()
            if not leagues:
                print('No leagues found. Make sure MCP server is running on localhost:8000', file=sys.stderr)
                sys.exit(1)

            league_id = leagues[0]['id']
            yahoo_rankings = get_sync_draft_rankings(league_id)
            aggregated = aggregate_rankings(yahoo_rankings, keepers)
            print(json.dumps(aggregated[:100], indent=2))
        except Exception as e:
            print(f'Error fetching Yahoo rankings: {e}', file=sys.stderr)
            print('Make sure FastMCP server is running: python3 fastmcp_server.py', file=sys.stderr)
            sys.exit(1)
        return

    if args.command == 'refresh-yahoo-rankings':
        try:
            leagues = get_sync_leagues()
            if not leagues:
                print('No leagues found. Make sure MCP server is running on localhost:8000', file=sys.stderr)
                sys.exit(1)

            league_id = leagues[0]['id']
            rankings = get_sync_draft_rankings(league_id)
            save_yahoo_rankings(rankings)
            print(f'Saved {len(rankings)} Yahoo rankings to data/raw/rankings/yahoo_rankings.json')
        except Exception as e:
            print(f'Error refreshing Yahoo rankings: {e}', file=sys.stderr)
            print('Make sure FastMCP server is running: python3 fastmcp_server.py', file=sys.stderr)
            sys.exit(1)
        return

    if args.command == 'import-rankings-csv':
        csv_path = Path(args.csv_path)
        rankings = load_rankings_csv(csv_path, source=args.source)
        save_yahoo_rankings(rankings)
        print(f'Saved {len(rankings)} rankings from {csv_path} to data/raw/rankings/yahoo_rankings.json')
        return

    if args.command == 'apply-qb-adjustment':
        rankings = load_yahoo_rankings()
        if not rankings:
            print('No saved rankings found. Run import-rankings-csv first.', file=sys.stderr)
            sys.exit(1)

        targets = compute_historical_qb_pick_targets(years=args.years)
        if not targets:
            print('No draft history with roster data available to compute historical QB targets.', file=sys.stderr)
            sys.exit(1)

        before = {
            entry['playerName']: entry['ranking']
            for entry in sorted((e for e in rankings if e.get('position') == 'QB'), key=lambda e: e.get('ranking', 9999))[:args.top_n]
        }

        adjusted = apply_qb_historical_adjustment(rankings, top_n=args.top_n, targets=targets)
        save_yahoo_rankings(adjusted)
        print(f'Saved adjusted rankings to {YAHOO_RANKINGS_FILE}')

        if not args.no_sync_combined:
            combined = []
            for entry in adjusted:
                source = entry.get('source', 'unknown')
                ranking = entry['ranking']
                combined.append({
                    'playerId': normalize_player_id(entry['playerName']),
                    'playerName': entry['playerName'],
                    'position': entry['position'],
                    'team': entry.get('team', 'UNK'),
                    'sourceRanks': {source: ranking},
                    'posRank': entry.get('posRank', ''),
                    'averageRank': float(ranking),
                    'ranking': ranking,
                    'sourceCount': 1,
                })
            combined.sort(key=lambda x: x['averageRank'])
            ensure_parent_dir(RANKINGS_COMBINED_FILE)
            RANKINGS_COMBINED_FILE.write_text(json.dumps(combined, indent=2))
            print(f'Mirrored into {RANKINGS_COMBINED_FILE} (single-source, for keepers-board-export)')

        print(f'\nQB adjustment (top {args.top_n}, historical targets from years={args.years or "all available"}):')
        print(f"{'Player':<22}{'Before':<9}{'Target':<9}{'After'}")
        for name, before_rank in before.items():
            hit = next(e for e in adjusted if e['playerName'] == name)
            target_idx = list(before.keys()).index(name)
            target = targets[target_idx] if target_idx < len(targets) else targets[-1]
            print(f"{name:<22}{before_rank:<9}{target:<9}{hit['ranking']}")
        return

    if args.command == 'import-rankings-pdf':
        pdf_path = Path(args.pdf_path)
        try:
            rankings = load_rankings_pdf(pdf_path, source=args.source)
        except ValueError as exc:
            print(f'Error parsing PDF: {exc}', file=sys.stderr)
            sys.exit(1)
        save_yahoo_rankings(rankings)
        print(f'Saved {len(rankings)} rankings from {pdf_path} to data/raw/rankings/yahoo_rankings.json')
        return

    if args.command == 'set-league-format':
        league_format = LeagueFormat(
            teams=args.teams,
            starters={
                'QB': args.qb,
                'RB': args.rb,
                'WR': args.wr,
                'TE': args.te,
                'FLEX': args.flex,
                'SUPERFLEX': args.superflex,
                'DEF': args.defense,
                'K': args.kicker,
            },
        )
        save_league_format(league_format)
        print(f'Saved league format: {league_format.summary()}')
        return

    if args.command == 'show-league-format':
        print(load_league_format().summary())
        return

    if args.command == 'keeper-insight':
        roster_players, used_saved = load_saved_or_api_roster(None)
        if used_saved:
            print(f'Loaded {len(roster_players)} roster players from data/raw/rosters/yahoo_roster.json')
        rankings = None
        if RANKINGS_COMBINED_FILE.exists():
            rankings = json.loads(RANKINGS_COMBINED_FILE.read_text())
        if not rankings:
            rankings = load_yahoo_rankings()
        if not rankings:
            print('No saved rankings found. Run combine-rankings or refresh-yahoo-rankings first.', file=sys.stderr)
            sys.exit(1)
        league_format = load_league_format()
        if args.teams != league_format.teams:
            league_format.teams = args.teams
        print(f'Using league format: {league_format.summary()}')
        if not league_format.keeper_cost_uses_draft_round:
            print(f'Keeper cost rule: none; keepers use the last draft slots and can be held for up to {league_format.keeper_max_consecutive_seasons} consecutive seasons.')
        insight = roster_keeper_insight(roster_players, rankings, teams=args.teams, league_format=league_format)
        print_keeper_insight(insight)
        return

    if args.command == 'best-keepers':
        roster_players, used_saved = load_saved_or_api_roster(None)
        if used_saved:
            print(f'Loaded {len(roster_players)} roster players from data/raw/rosters/yahoo_roster.json')
        rankings = None
        if RANKINGS_COMBINED_FILE.exists():
            rankings = json.loads(RANKINGS_COMBINED_FILE.read_text())
        if not rankings:
            rankings = load_yahoo_rankings()
        if not rankings:
            print('No saved rankings found. Run combine-rankings or refresh-yahoo-rankings first.', file=sys.stderr)
            sys.exit(1)
        league_format = load_league_format()
        if args.teams != league_format.teams:
            league_format.teams = args.teams
        print(f'Using league format: {league_format.summary()}')
        if not league_format.keeper_cost_uses_draft_round:
            print(f'Keeper cost rule: none; keepers use the last draft slots and can be held for up to {league_format.keeper_max_consecutive_seasons} consecutive seasons.')
        insight = roster_keeper_insight(roster_players, rankings, teams=args.teams, league_format=league_format)
        chosen, alternates = select_best_keepers(insight, keeper_count=args.count, league_format=league_format)
        print_best_keepers(chosen, alternates)
        return

    if args.command == 'forecast-keepers':
        try:
            league_rosters = json.loads(YAHOO_LEAGUE_ROSTERS_JSON.read_text())
        except FileNotFoundError:
            print(f'No saved league roster snapshot at {YAHOO_LEAGUE_ROSTERS_JSON}. Run scrape-league-rosters first.', file=sys.stderr)
            sys.exit(1)
        rankings = None
        if RANKINGS_COMBINED_FILE.exists():
            rankings = json.loads(RANKINGS_COMBINED_FILE.read_text())
        if not rankings:
            rankings = load_yahoo_rankings()
        if not rankings:
            print('No saved rankings found. Run combine-rankings or refresh-yahoo-rankings first.', file=sys.stderr)
            sys.exit(1)
        roster_players, _used_saved = load_saved_or_api_roster(None)
        league_format = load_league_format()
        if args.teams != league_format.teams:
            league_format.teams = args.teams
        likely_keepers, top_available = forecast_opponent_keepers(
            league_rosters,
            rankings,
            league_format,
            your_roster_players=roster_players,
            keeper_count=args.keepers_per_team,
            consider_top=args.top,
        )
        print_forecast_results(likely_keepers, top_available)
        return

    if args.command == 'yahoo-roster':
        try:
            leagues = get_sync_leagues()
            if not leagues:
                print('No leagues found. Make sure MCP server is running on localhost:8000', file=sys.stderr)
                sys.exit(1)

            league_id = leagues[0]['id']
            roster_players = get_sync_roster(league_id)
            print(json.dumps([player.__dict__ for player in roster_players], indent=2))
        except Exception as e:
            print(f'Error fetching roster: {e}', file=sys.stderr)
            print('Make sure FastMCP server is running: python3 fastmcp_server.py', file=sys.stderr)
            sys.exit(1)
        return

    if args.command == 'save-roster':
        try:
            leagues = get_sync_leagues()
            if not leagues:
                print('No leagues found. Make sure MCP server is running on localhost:8000', file=sys.stderr)
                sys.exit(1)

            league_id = leagues[0]['id']
            roster_players = get_sync_roster(league_id)
            save_roster([player.__dict__ for player in roster_players])
            print(f'Saved {len(roster_players)} roster players to data/raw/rosters/yahoo_roster.json')
        except Exception as e:
            print(f'Error saving roster: {e}', file=sys.stderr)
            print('Make sure FastMCP server is running: python3 fastmcp_server.py', file=sys.stderr)
            sys.exit(1)
        return

    if args.command == 'fetch-league-rosters-mcp':
        try:
            leagues = get_sync_leagues()
            if not leagues:
                print('No leagues found. Make sure MCP server is running on localhost:8000', file=sys.stderr)
                sys.exit(1)

            league_id = leagues[0]['id']
            print(f'Fetching all team rosters for league {league_id}...')
            all_rosters = get_sync_all_team_rosters(league_id)

            if not all_rosters:
                print('No rosters found', file=sys.stderr)
                sys.exit(1)

            league_rosters = []
            for _team_key, (team_name, roster_players) in all_rosters.items():
                team_roster = {
                    'teamName': team_name,
                    'playerCount': len(roster_players),
                    'players': [player.__dict__ for player in roster_players],
                }
                league_rosters.append(team_roster)

            output_path = Path(args.output)
            ensure_parent_dir(output_path)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(league_rosters, f, indent=2)

            print(f'Saved {len(league_rosters)} team rosters to {args.output}')
            for team_roster in league_rosters:
                print(f"  {team_roster['teamName']}: {team_roster['playerCount']} players")
        except Exception as e:
            print(f'Error fetching league rosters: {e}', file=sys.stderr)
            print('Make sure FastMCP server is running: python3 fastmcp_server.py', file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)
        return

    if args.command == 'scrape-roster':
        if not config.yahoo_email or not config.yahoo_password:
            print('Error: YAHOO_EMAIL and YAHOO_PASSWORD must be set in .env', file=sys.stderr)
            sys.exit(1)
        print(f'Scraping roster for {config.yahoo_email}...')
        roster_players = scrape_roster(config.yahoo_email, config.yahoo_password, headless=args.headless)
        if roster_players:
            save_roster([player.__dict__ for player in roster_players])
            print(f'Saved {len(roster_players)} roster players to data/raw/rosters/yahoo_roster.json')
        else:
            print('Failed to scrape roster', file=sys.stderr)
            sys.exit(1)
        return

    if args.command == 'scrape-league-rosters':
        if not config.yahoo_email or not config.yahoo_password:
            print('Error: YAHOO_EMAIL and YAHOO_PASSWORD must be set in .env', file=sys.stderr)
            sys.exit(1)
        print(f'Scraping all {args.teams} team rosters for {config.yahoo_email}...')
        league_rosters = scrape_league_rosters(config.yahoo_email, config.yahoo_password, team_count=args.teams, headless=args.headless)
        if league_rosters:
            output_path = Path(args.output)
            ensure_parent_dir(output_path)
            output_path.write_text(json.dumps(league_rosters, indent=2), encoding='utf-8')
            total_players = sum(team.get('playerCount', 0) for team in league_rosters)
            print(f'Saved {len(league_rosters)} team rosters and {total_players} players to {output_path}')
        else:
            print('Failed to scrape league rosters', file=sys.stderr)
            sys.exit(1)
        return

    if args.command == 'export-league-rosters-csv':
        input_path = Path(args.input)
        output_path = Path(args.output)
        row_count = export_league_rosters_csv(input_path, output_path)
        print(f'Saved {row_count} roster rows to {output_path}')
        return

    if args.command == 'saved-roster':
        roster_players = load_roster()
        if not roster_players:
            print('No saved roster found. Run save-roster to persist your Yahoo roster locally.', file=sys.stderr)
            sys.exit(1)
        print(json.dumps([player.__dict__ for player in roster_players], indent=2))
        return

    if args.command == 'yahoo-keepers':
        try:
            leagues = get_sync_leagues()
            if not leagues:
                print('No leagues found. Make sure MCP server is running on localhost:8000', file=sys.stderr)
                sys.exit(1)

            league_id = leagues[0]['id']
            roster = get_sync_roster(league_id)
            # Filter to keeper-eligible players
            keepers = [p for p in roster if p.keeperEligibleOverride is not False]
            print(json.dumps([player.__dict__ for player in keepers], indent=2))
        except Exception as e:
            print(f'Error fetching keepers: {e}', file=sys.stderr)
            print('Make sure FastMCP server is running: python3 fastmcp_server.py', file=sys.stderr)
            sys.exit(1)
        return

    if args.command == 'roster-raw':
        access_token = resolve_access_token(args.access_token)
        roster = get_roster(access_token)
        print(json.dumps(roster, indent=2))
        return

    if args.command == 'set-lineup':
        access_token = resolve_access_token(args.access_token)
        lineup = parse_lineup_json(args.lineup_json)
        result = set_lineup(access_token, lineup)
        print(json.dumps(result, indent=2))
        return

    if args.command == 'draft-history':
        years = load_draft_years()
        picks = years.get(args.year)
        if picks is None:
            print(f'No saved draft history for {args.year}. Add data/raw/draft_history/{args.year}.json.', file=sys.stderr)
            sys.exit(1)
        if args.keepers_only:
            picks = keeper_slot_picks(args.year, years)
        elif args.live_only:
            picks = live_draft_picks(args.year, years)
        if args.round is not None:
            picks = [p for p in picks if p.get('round') == args.round]
        for p in sorted(picks, key=lambda p: (p.get('round', 0), p.get('pick', 0))):
            print(f"R{p.get('round')}.{p.get('pick')}  {p.get('playerName')}  -> {p.get('team')}")
        return

    if args.command == 'draft-picks':
        picks = load_draft_picks(args.year)
        if picks is None:
            print(f'No saved pick ownership for {args.year}. Add data/raw/draft_picks/{args.year}.json.', file=sys.stderr)
            sys.exit(1)
        teams = [args.team] if args.team else sorted(picks.keys())
        for team in teams:
            rounds = picks.get(team)
            if rounds is None:
                print(f'Unknown team: {team}', file=sys.stderr)
                continue
            counts = ' '.join(str(rounds.get(r, 0)) for r in sorted(rounds.keys()))
            print(f'{team:<30} {counts}')
        return

    if args.command == 'standings':
        standings = load_standings(args.year)
        if standings is None:
            print(f'No saved standings for {args.year}. Add data/raw/standings/{args.year}.json.', file=sys.stderr)
            sys.exit(1)
        for row in standings:
            playoffs = ' (playoffs)' if row.get('madePlayoffs') else ''
            print(
                f"{row.get('rank'):>2}. {row.get('team'):<30} "
                f"{row.get('wins')}-{row.get('losses')}-{row.get('ties')}  "
                f"PF {row.get('pointsFor')}  PA {row.get('pointsAgainst')}{playoffs}"
            )
        return

    if args.command == 'draft-order':
        standings = load_standings(args.standings_year)
        if standings is None:
            print(f'No saved standings for {args.standings_year}. Add data/raw/standings/{args.standings_year}.json.', file=sys.stderr)
            sys.exit(1)
        round1_order = draft_order_from_standings(standings)
        snake = snake_draft_order(round1_order, args.rounds)
        for round_number in sorted(snake.keys()):
            print(f'Round {round_number}:')
            for i, team in enumerate(snake[round_number], start=1):
                print(f'  {i}. {team}')
        return

    if args.command == 'keepers-board':
        input_path = Path(args.input)
        try:
            league_rosters = json.loads(input_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            print(f'League roster snapshot not found: {input_path}', file=sys.stderr)
            sys.exit(1)
        rankings = load_yahoo_rankings()
        if not rankings:
            print('No saved Yahoo rankings found. Run refresh-yahoo-rankings or import-rankings-csv first.', file=sys.stderr)
            sys.exit(1)
        league_format = load_league_format()
        if args.teams != league_format.teams:
            league_format.teams = args.teams

        per_team, remaining_board = league_keeper_board(league_rosters, rankings, league_format, keeper_count=args.count)
        for entry in per_team:
            picks = ', '.join(f"{c['playerName']} ({c.get('ranking')})" for c in entry['chosen'])
            print(f"{entry['team']}: {picks}")

        output_path = Path(args.output)
        ensure_parent_dir(output_path)
        with output_path.open('w', newline='', encoding='utf-8') as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=['draftOrder', 'ranking', 'playerName', 'position', 'posRank', 'team', 'source'])
            writer.writeheader()
            for row in remaining_board:
                writer.writerow({
                    'draftOrder': row['draftOrder'],
                    'ranking': row.get('ranking'),
                    'playerName': row.get('playerName'),
                    'position': row.get('position'),
                    'posRank': row.get('posRank'),
                    'team': row.get('team'),
                    'source': row.get('source'),
                })
        keepers_removed = sum(len(entry['chosen']) for entry in per_team)
        print(f'\nWrote {len(remaining_board)} players to {output_path} ({keepers_removed} keepers removed)')
        return

    if args.command == 'list-league-keys':
        access_token = resolve_access_token(None)
        try:
            games = fetch_games(access_token, args.seasons)
            print('\nGame keys by season:')
            for year in sorted(games.keys()):
                print(f'  {year}: {games[year]}')

            league_keys_by_year = {}
            for year, game_key in sorted(games.items()):
                print(f'\nFetching leagues for {year}...')
                leagues = fetch_user_leagues(access_token, game_key)
                if leagues:
                    for league_id, info in leagues.items():
                        league_key = info.get('league_key')
                        league_name = info.get('name')
                        if league_key:
                            league_keys_by_year[year] = league_key
                            print(f'  {year}: {league_key} ({league_name})')

            print('\nReady to fetch standings. Use: fetch-standings <year> --league-key <key>')
        except Exception as e:
            print(f'Error resolving league keys: {e}', file=sys.stderr)
            sys.exit(1)
        return

    if args.command == 'fetch-standings':
        access_token = resolve_access_token(None)
        try:
            league_key = args.league_key
            if not league_key:
                print('Error: --league-key is required (run list-league-keys to resolve)', file=sys.stderr)
                sys.exit(1)

            data = fetch_standings(access_token, league_key)
            if data is None:
                print('Failed to fetch standings', file=sys.stderr)
                sys.exit(1)

            output_path = Path('data/raw/standings') / f'{args.year}.json'
            ensure_parent_dir(output_path)
            output_path.write_text(json.dumps(data, indent=2))
            print(f'Wrote standings for {args.year} to {output_path}')
            print('\nTeams:')
            for row in data.get('standings', []):
                print(f"  {row.get('rank'):>2}. {row.get('team'):<30} {row.get('wins')}-{row.get('losses')}-{row.get('ties')}")
        except Exception as e:
            print(f'Error fetching standings: {e}', file=sys.stderr)
            sys.exit(1)
        return

    if args.command == 'backfill-standings':
        access_token = resolve_access_token(None)
        try:
            games = fetch_games(access_token, list(range(args.start, args.end + 1)))
            print(f'\nBackfilling standings for {args.start}-{args.end}...')

            for year in range(args.start, args.end + 1):
                game_key = games.get(year)
                if not game_key:
                    print(f'  {year}: No game_key found', file=sys.stderr)
                    continue

                leagues = fetch_user_leagues(access_token, game_key)
                if not leagues:
                    print(f'  {year}: No leagues found', file=sys.stderr)
                    continue

                league_key = next(iter(info.get('league_key') for info in leagues.values() if info.get('league_key')), None)
                if not league_key:
                    print(f'  {year}: No league_key found', file=sys.stderr)
                    continue

                data = fetch_standings(access_token, league_key)
                if data is None:
                    print(f'  {year}: Failed to fetch standings', file=sys.stderr)
                    continue

                output_path = Path('data/raw/standings') / f'{year}.json'
                ensure_parent_dir(output_path)
                output_path.write_text(json.dumps(data, indent=2))
                print(f'  {year}: Wrote {len(data.get("standings", []))} teams')

            print('\nBackfill complete')
        except Exception as e:
            print(f'Error backfilling standings: {e}', file=sys.stderr)
            sys.exit(1)
        return

    if args.command == 'scrape-standings':
        email = args.email or config.yahoo_email
        password = args.password or config.yahoo_password
        if not email or not password:
            print('Error: Yahoo email and password required. Provide via --email --password or set YAHOO_EMAIL/YAHOO_PASSWORD in .env', file=sys.stderr)
            sys.exit(1)

        try:
            data = scrape_standings(email, password, season=args.year, headless=args.headless)
            if data is None:
                print(f'Failed to scrape standings for {args.year}', file=sys.stderr)
                sys.exit(1)

            output_path = Path('data/raw/standings') / f'{args.year}.json'
            ensure_parent_dir(output_path)
            output_path.write_text(json.dumps(data, indent=2))
            print(f'\nWrote standings for {args.year} to {output_path}')
            print('\nTeams:')
            for row in data.get('standings', []):
                print(f"  {row.get('rank'):>2}. {row.get('team'):<30} {row.get('wins')}-{row.get('losses')}-{row.get('ties')}")
        except Exception as e:
            print(f'Error scraping standings: {e}', file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)
        return

    if args.command == 'backfill-standings':
        email = args.email or config.yahoo_email
        password = args.password or config.yahoo_password
        if not email or not password:
            print('Error: Yahoo email and password required. Provide via --email --password or set YAHOO_EMAIL/YAHOO_PASSWORD in .env', file=sys.stderr)
            sys.exit(1)

        try:
            print(f'Backfilling standings for {args.start}-{args.end}...')
            for year in range(args.start, args.end + 1):
                print(f'\n{year}:')
                data = scrape_standings(email, password, season=year, headless=args.headless)
                if data is None:
                    print('  Failed to scrape standings', file=sys.stderr)
                    continue

                output_path = Path('data/raw/standings') / f'{year}.json'
                ensure_parent_dir(output_path)
                output_path.write_text(json.dumps(data, indent=2))
                print(f'  Wrote {len(data.get("standings", []))} teams')

            print('\nBackfill complete')
        except Exception as e:
            print(f'Error backfilling standings: {e}', file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)
        return

    if args.command == 'fetch-nfl-stats':
        try:
            seasons = None
            if args.seasons:
                seasons = args.seasons
            elif args.start_season or args.end_season:
                start = args.start_season or 2019
                end = args.end_season or 2026
                seasons = list(range(start, end + 1))

            result = refresh_nfl_stats(seasons)
            print('\nNFL stats fetched:')
            for datatype, paths_by_season in result.items():
                print(f'  {datatype}:')
                for season, path in sorted(paths_by_season.items()):
                    print(f'    {season}: {path}')
        except Exception as e:
            print(f'Error fetching NFL stats: {e}', file=sys.stderr)
            sys.exit(1)
        return

    if args.command == 'draft-slot-outcomes':
        outcomes = draft_slot_vs_final_rank()
        if not outcomes:
            print('No outcomes found. Ensure draft_history and standings data exist.', file=sys.stderr)
            sys.exit(1)

        summary = summarize_draft_slot_correlation(outcomes)
        print('\n=== Draft Slot vs Final Rank ===')
        print(f'Samples: {summary.get("n_samples")} (12 teams per year × years with both draft and standings data)')
        print(f'Correlation: {summary.get("correlation")}')
        print('\nSlot-to-Rank Average:')
        for slot in sorted(summary.get('slot_to_avg', {}).keys()):
            data = summary['slot_to_avg'][slot]
            print(f'  Slot {slot:2d}: avg rank {data["avg_rank"]:5.1f} (median {int(data["median_rank"]):2d}, n={data["n"]})')

        print(f'\nNote: {summary.get("caveat")}')

        if args.export_csv:
            ensure_parent_dir(Path(args.export_csv))
            with open(args.export_csv, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=['year', 'team', 'draft_slot', 'final_rank'])
                writer.writeheader()
                for outcome in outcomes:
                    writer.writerow(outcome.__dict__)
            print(f'\nExported to {args.export_csv}')
        return

    if args.command == 'position-round-outcomes':
        outcomes = position_in_round_vs_final_rank(args.round_number)
        if not outcomes:
            print(f'No outcomes found for round {args.round_number}.', file=sys.stderr)
            sys.exit(1)

        summary = summarize_position_in_round(outcomes)
        print(f'\n=== Position Drafted in Round {args.round_number} vs Final Rank ===')
        print(f'Total picks analyzed: {len(outcomes)}')
        print('\nAverage Final Rank by Position:')
        for pos in sorted(summary.keys()):
            data = summary[pos]
            print(f'  {pos:4s}: avg {data["avg_rank"]:5.1f} (median {int(data["median_rank"]):2d}, n={data["n"]})')

        print('\nNote: Small sample sizes. Positions drafted in fewer than 3 instances may not be meaningful.')

        if args.export_csv:
            ensure_parent_dir(Path(args.export_csv))
            with open(args.export_csv, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=['year', 'team', 'round', 'position', 'final_rank'])
                writer.writeheader()
                for outcome in outcomes:
                    writer.writerow(outcome.__dict__)
            print(f'\nExported to {args.export_csv}')
        return

    if args.command == 'combine-rankings':
        try:
            output_path = combine_rankings_all()
            if args.output:
                import shutil
                shutil.copy(output_path, args.output)
                print(f'Also copied to {args.output}')
        except Exception as e:
            print(f'Error combining rankings: {e}', file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)
        return

    if args.command == 'adjust-rankings':
        try:
            config_path = Path(args.config) if args.config else None
            json_path, csv_path = adjust_rankings(config_path)
            print(f'Adjusted rankings saved to {json_path}')
            print(f'Comparison CSV saved to {csv_path}')
        except Exception as e:
            print(f'Error adjusting rankings: {e}', file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)
        return

    if args.command == 'extract-keeper-history':
        try:
            output_path = save_keeper_history()
            print(f'Keeper history extracted to {output_path}')

            keeper_history = load_keeper_history(output_path)
            print(f'\nKeeper history summary:')
            for team in sorted(keeper_history.keys()):
                seasons = keeper_history[team]
                print(f'  {team}: {len(seasons)} seasons')
                for year in sorted(seasons.keys()):
                    players = seasons[year]
                    print(f'    {year}: {", ".join(players)}')
        except Exception as e:
            print(f'Error extracting keeper history: {e}', file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)
        return

    if args.command == 'manager-keeper-history':
        try:
            output_path = save_manager_profiles_with_keepers()
            print(f'Manager profiles with keeper history saved to {output_path}')

            with open(output_path, encoding='utf-8') as f:
                profiles = json.load(f)

            # Show summary
            print(f'\nGenerated profiles for {sum(len(p) for p in profiles.values())} manager-years')
            for year in sorted(profiles.keys(), reverse=True):
                year_profiles = profiles[year]
                keepers_count = sum(1 for p in year_profiles if 'keepers_by_year' in p)
                print(f'  {year}: {len(year_profiles)} teams, {keepers_count} with keeper history')
        except Exception as e:
            print(f'Error generating manager profiles: {e}', file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)
        return

    if args.command == 'keeper-strategy':
        try:
            keeper_history = load_keeper_history()
            strategy = get_team_keeper_strategy(keeper_history, args.team)

            if not strategy.get('found'):
                print(f'No keeper history found for team: {args.team}', file=sys.stderr)
                print(f'Available teams: {", ".join(sorted(keeper_history.keys()))}', file=sys.stderr)
                sys.exit(1)

            print(f'Keeper strategy for {strategy["team"]}:')
            print(f'  Seasons with data: {strategy["seasons_with_data"]}')
            print(f'  Mode keeper count: {strategy["mode_keeper_count"]}')
            print(f'  Keeper count by year:')
            for year, count in sorted(strategy['keeper_counts_by_year'].items()):
                print(f'    {year}: {count}')
        except FileNotFoundError:
            print('Keeper history not found. Run extract-keeper-history first.', file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f'Error analyzing keeper strategy: {e}', file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)
        return

    if args.command == 'map-teams':
        try:
            output_path = save_team_mapping()
            print(f'Team mapping saved to {output_path}')

            mapping = load_team_mapping(output_path)
            print(f'\nTeam identity mapping ({len(mapping)} owners):')
            for owner_id in sorted(mapping.keys()):
                owner_data = mapping[owner_id]
                print(f'  {owner_id} (slot {owner_data.get("standing_position")}):')
                for year in sorted(owner_data.get('names_by_year', {}).keys()):
                    name = owner_data['names_by_year'][year]
                    print(f'    {year}: {name}')
        except Exception as e:
            print(f'Error mapping teams: {e}', file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)
        return

    if args.command == 'refresh-oauth-token':
        try:
            refresh_tok = os.getenv('YAHOO_REFRESH_TOKEN')
            if not refresh_tok:
                print('Error: YAHOO_REFRESH_TOKEN not found in .env', file=sys.stderr)
                sys.exit(1)

            print('Refreshing Yahoo OAuth token...')
            new_token = refresh_token(refresh_tok)

            # Update .env file
            env_file = Path('.env')
            if env_file.exists():
                with open(env_file, 'r', encoding='utf-8') as f:
                    env_content = f.read()

                # Replace token lines
                env_content = re.sub(
                    r'YAHOO_ACCESS_TOKEN=.*',
                    f'YAHOO_ACCESS_TOKEN={new_token.access_token}',
                    env_content
                )
                env_content = re.sub(
                    r'YAHOO_REFRESH_TOKEN=.*',
                    f'YAHOO_REFRESH_TOKEN={new_token.refresh_token}',
                    env_content
                )

                with open(env_file, 'w', encoding='utf-8') as f:
                    f.write(env_content)

                print(f'✓ Token refreshed and saved to .env')
                print(f'  Access token expires in: {new_token.expires_in} seconds')
            else:
                print('Error: .env file not found', file=sys.stderr)
                sys.exit(1)
        except Exception as e:
            print(f'Error refreshing token: {e}', file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)
        return

    if args.command == 'backfill-standings-mcp':
        try:
            from .standings import RAW_STANDINGS_DIR
            leagues = get_sync_leagues()
            if not leagues:
                print('No leagues found. Make sure MCP server is running', file=sys.stderr)
                sys.exit(1)

            league_id = leagues[0]['id']
            print(f'Fetching standings for league {league_id} ({args.start_year}-{args.end_year})...')

            saved_count = 0
            for year in range(args.start_year, args.end_year + 1):
                try:
                    standings = get_sync_standings_for_year(league_id, year)
                    if standings:
                        output_file = RAW_STANDINGS_DIR / f'{year}.json'
                        output_file.parent.mkdir(parents=True, exist_ok=True)
                        with open(output_file, 'w', encoding='utf-8') as f:
                            json.dump({
                                'year': year,
                                'standings': standings,
                            }, f, indent=2)
                        print(f'  {year}: saved {len(standings)} standings')
                        saved_count += 1
                    else:
                        print(f'  {year}: no data found')
                except Exception as e:
                    print(f'  {year}: error - {e}')

            print(f'\nBackfilled {saved_count} years of standings to {RAW_STANDINGS_DIR}')
        except Exception as e:
            print(f'Error backfilling standings: {e}', file=sys.stderr)
            print('Make sure MCP server is running', file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)
        return

    if args.command == 'fantasypros-rankings':
        try:
            fantasypros_fetch_rankings(sport=args.sport, season=args.season, week=args.week)
            print('Rankings saved to data/raw/rankings/fantasypros_*.json')
            print('Run combine-rankings to merge with other sources.')
        except ValueError as e:
            print(f'Error: {e}', file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f'Error fetching FantasyPros rankings: {e}', file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)
        return

    if args.command == 'fantasypros-projections':
        try:
            positions = args.positions.split(',')
            fantasypros_fetch_projections(sport=args.sport, season=args.season, positions=positions, week=args.week)
            print('Projections saved to data/raw/rankings/fantasypros_*_projections*.json')
        except ValueError as e:
            print(f'Error: {e}', file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f'Error fetching FantasyPros projections: {e}', file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)
        return

    if args.command == 'build-features':
        try:
            seasons = args.seasons or [2022, 2023, 2024, 2025]
            output_path = build_and_save_feature_table(seasons, output_path=Path(args.output) if args.output else None)
            print(f'Feature table ready for ML analysis at {output_path}')
        except Exception as e:
            print(f'Error building feature table: {e}', file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)
        return

    if args.command == 'parse-rosters':
        print('Paste your Yahoo Fantasy rosters (copy-pasted from browser). Press Ctrl+D (or Ctrl+Z on Windows) when done:\n')
        lines = []
        try:
            while True:
                line = input()
                lines.append(line)
        except EOFError:
            pass

        text = '\n'.join(lines)
        if not text.strip():
            print('No text provided.', file=sys.stderr)
            sys.exit(1)

        teams = parse_yahoo_text_rosters(text)
        if not teams:
            print('No teams parsed from input.', file=sys.stderr)
            sys.exit(1)

        print(f'\nParsed {len(teams)} teams:')
        print(format_roster_preview(teams))

        response = input('\nSave to data/raw/rosters/yahoo_league_rosters.json? (y/n): ').strip().lower()
        if response == 'y':
            output_path = YAHOO_LEAGUE_ROSTERS_JSON
            ensure_parent_dir(output_path)
            rosters_data = []
            for i, team in enumerate(teams, 1):
                rosters_data.append({
                    'teamId': i,
                    'ownerName': '',
                    'teamName': team['teamName'],
                    'playerCount': len(team['players']),
                    'players': team['players'],
                })
            output_path.write_text(json.dumps(rosters_data, indent=2))
            print(f'Saved {len(rosters_data)} team rosters to {output_path}')
        else:
            print('Rosters not saved.')
        return

    if args.command == 'import-adp':
        try:
            csv_path = Path(args.csv_path)
            import_and_save_adp(csv_path)
            print('ADP saved to data/raw/adp/adp_combined.json')
            print('Run adp-value-analysis to compare rankings vs ADP.')
        except FileNotFoundError:
            print(f'File not found: {args.csv_path}', file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f'Error importing ADP: {e}', file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)
        return

    if args.command == 'adp-value-analysis':
        # Load rankings
        if not RANKINGS_COMBINED_FILE.exists():
            print('No combined rankings found. Run combine-rankings first.', file=sys.stderr)
            sys.exit(1)
        rankings = json.loads(RANKINGS_COMBINED_FILE.read_text())

        # Load ADP
        adp_data = load_adp_json()
        if not adp_data:
            print('No ADP data found. Run import-adp first.', file=sys.stderr)
            sys.exit(1)

        # Analyze
        analysis = rank_vs_adp_analysis(rankings, adp_data)
        print(f'Matched {len(analysis)} players (experts vs market)')
        print()

        # Show undervalued
        undervalued = [e for e in analysis if e['delta'] > 3]
        print(f'UNDERVALUED (ranked higher than ADP, δ > +3): {len(undervalued)} players')
        print('-' * 105)
        for entry in undervalued[:20]:
            print(
                f"  {entry['rank']:3d}. rank vs {entry['adp']:6.1f} ADP  │  "
                f"{entry['playerName']:25} {entry['position']:4}  │  +{entry['delta']:5.1f}"
            )

        print()

        # Show overvalued
        overvalued = [e for e in analysis if e['delta'] < -3]
        print(f'OVERVALUED (ranked lower than ADP, δ < -3): {len(overvalued)} players')
        print('-' * 105)
        for entry in sorted(overvalued, key=lambda x: x['delta'])[:20]:
            print(
                f"  {entry['rank']:3d}. rank vs {entry['adp']:6.1f} ADP  │  "
                f"{entry['playerName']:25} {entry['position']:4}  │  {entry['delta']:5.1f}"
            )

        # Export if requested
        if args.export:
            output_path = Path(args.export)
            ensure_parent_dir(output_path)
            with open(output_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=['playerName', 'position', 'team', 'rank', 'adp', 'delta', 'value', 'sources']
                )
                writer.writeheader()
                writer.writerows(analysis)
            print()
            print(f'Exported full analysis to {output_path}')
        return

    if args.command == 'keepers-board-export':
        try:
            league_rosters = json.loads(YAHOO_LEAGUE_ROSTERS_JSON.read_text())
        except FileNotFoundError:
            print(f'No saved league roster snapshot at {YAHOO_LEAGUE_ROSTERS_JSON}. Run parse-rosters first.', file=sys.stderr)
            sys.exit(1)

        # Load combined rankings (fallback to yahoo rankings if not available)
        rankings = None
        if RANKINGS_COMBINED_FILE.exists():
            rankings = json.loads(RANKINGS_COMBINED_FILE.read_text())
        if not rankings:
            rankings = load_yahoo_rankings()
        if not rankings:
            print('No saved rankings found. Run combine-rankings first.', file=sys.stderr)
            sys.exit(1)

        league_format = load_league_format()
        if args.teams != league_format.teams:
            league_format.teams = args.teams

        per_team, remaining_board = league_keeper_board(league_rosters, rankings, league_format, keeper_count=args.count)

        output_dir = Path(args.output_dir)
        ensure_parent_dir(output_dir)

        from datetime import datetime
        timestamp = datetime.now().strftime('%Y%m%d_%H%M')

        # Export per-team keepers
        keepers_file = output_dir / f'keepers_{timestamp}.csv'
        with open(keepers_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'Team', 'PlayerName', 'Position', 'Ranking', 'Status',
                'KeeperYearsRemaining', 'ValueOverReplacementRounds'
            ])
            writer.writeheader()
            for entry in per_team:
                team_name = entry['team']
                for i, keeper in enumerate(entry['chosen'], 1):
                    writer.writerow({
                        'Team': team_name,
                        'PlayerName': keeper['playerName'],
                        'Position': keeper['position'],
                        'Ranking': keeper['ranking'],
                        'Status': f'Keeper {i}',
                        'KeeperYearsRemaining': keeper.get('keeperYearsRemaining'),
                        'ValueOverReplacementRounds': keeper.get('valueOverReplacementRounds'),
                    })
                for i, alt in enumerate(entry['alternates'][:3], 1):
                    writer.writerow({
                        'Team': team_name,
                        'PlayerName': alt['playerName'],
                        'Position': alt['position'],
                        'Ranking': alt['ranking'],
                        'Status': f'Alt {i}',
                        'KeeperYearsRemaining': alt.get('keeperYearsRemaining'),
                        'ValueOverReplacementRounds': alt.get('valueOverReplacementRounds'),
                    })

        # Export post-keepers draft board
        board_file = output_dir / f'draft_board_{timestamp}.csv'
        with open(board_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'DraftOrder', 'PlayerName', 'Position', 'Ranking', 'PosRank', 'Team'
            ])
            writer.writeheader()
            for row in remaining_board:
                writer.writerow({
                    'DraftOrder': row.get('draftOrder'),
                    'PlayerName': row.get('playerName'),
                    'Position': row.get('position'),
                    'Ranking': row.get('ranking'),
                    'PosRank': row.get('posRank'),
                    'Team': row.get('team'),
                })

        print(f'Exported keeper recommendations to {keepers_file}')
        print(f'Exported post-keepers draft board to {board_file}')
        print(f'\nSummary:')
        print(f'  {len(per_team)} teams with keeper picks')
        print(f'  {len(remaining_board)} players available for draft')
        return

    print(f'Unsupported command: {args.command}', file=sys.stderr)
    sys.exit(1)


if __name__ == '__main__':
    main()
