"""
MCP client wrapper for Yahoo Fantasy Football.

This module uses OAuth tokens from the Fantasy Football MCP server's .env
file to make API calls through wuff's existing yahoo_client, avoiding
the need to manage tokens separately in wuff.
"""

import logging
from typing import Any, Dict, List, Optional
from pathlib import Path

from dotenv import load_dotenv
import os

from .yahoo_client import (
    YahooRosterPlayer,
    fetch_yahoo_rankings,
    fetch_yahoo_roster_players,
    fetch_yahoo_keepers,
    fetch_standings,
    fetch_user_leagues,
    fetch_games,
    fetch_league_teams,
    fetch_team_roster,
    fetch_all_team_rosters,
)

logger = logging.getLogger(__name__)

# Load OAuth credentials from the Fantasy Football MCP server's .env
MCP_ENV_PATH = Path(__file__).parent.parent.parent / "fantasy-football-mcp-public" / ".env"
if MCP_ENV_PATH.exists():
    load_dotenv(MCP_ENV_PATH)
    YAHOO_ACCESS_TOKEN = os.getenv('YAHOO_ACCESS_TOKEN')
else:
    YAHOO_ACCESS_TOKEN = None

if not YAHOO_ACCESS_TOKEN:
    logger.warning(
        f"No YAHOO_ACCESS_TOKEN found in {MCP_ENV_PATH}. "
        "MCP client will not work. Make sure the Fantasy Football MCP server is configured."
    )


def _get_access_token() -> str:
    """Get the current Yahoo access token."""
    if not YAHOO_ACCESS_TOKEN:
        raise RuntimeError(
            f"No Yahoo access token configured. "
            f"Make sure {MCP_ENV_PATH} exists and has YAHOO_ACCESS_TOKEN set."
        )
    return YAHOO_ACCESS_TOKEN


def get_sync_leagues() -> List[Dict[str, Any]]:
    """
    Get all fantasy football leagues for the authenticated user.

    Returns:
        List of league dictionaries with id, name, scoring_type, etc.
    """
    import datetime

    token = _get_access_token()
    game_key = 'nfl'

    try:
        # Try current year first, then previous year if no leagues found
        current_year = datetime.datetime.now().year
        games = fetch_games(token, [current_year])

        if not games:
            # Try previous year if current year has no leagues
            games = fetch_games(token, [current_year - 1])

        if games:
            game_key = list(games.values())[0]

        leagues = fetch_user_leagues(token, game_key)

        # Convert to standard format
        result = []
        for league_key, league_data in leagues.items():
            result.append({
                'id': league_key,
                'name': league_data.get('name', ''),
                'scoring_type': league_data.get('scoring_type', 'PPR'),
            })
        return result
    except Exception as e:
        logger.error(f"Error fetching leagues: {e}")
        raise


def get_sync_standings(league_id: str) -> List[Dict[str, Any]]:
    """
    Get current league standings.

    Args:
        league_id: Yahoo league ID

    Returns:
        List of team standings with wins, losses, points for, etc.
    """
    token = _get_access_token()
    try:
        standings = fetch_standings(token, league_id)
        return standings or []
    except Exception as e:
        logger.error(f"Error fetching standings: {e}")
        raise


def get_sync_roster(league_id: str, team_key: Optional[str] = None) -> List[YahooRosterPlayer]:
    """
    Get roster players for a team.

    Args:
        league_id: Yahoo league ID
        team_key: Optional specific team key. If not provided, fetches user's team.

    Returns:
        List of YahooRosterPlayer objects on the roster
    """
    token = _get_access_token()
    try:
        roster_players = fetch_yahoo_roster_players(token)
        return roster_players or []
    except Exception as e:
        logger.error(f"Error fetching roster: {e}")
        raise


def get_sync_draft_rankings(league_id: str) -> List[Dict[str, Any]]:
    """
    Get pre-draft rankings for the league.

    Args:
        league_id: Yahoo league ID

    Returns:
        List of players with ADP and expert rankings
    """
    token = _get_access_token()
    try:
        rankings = fetch_yahoo_rankings(token)
        return rankings or []
    except Exception as e:
        logger.error(f"Error fetching draft rankings: {e}")
        raise


def get_sync_keepers(league_id: str) -> List[YahooRosterPlayer]:
    """
    Get keeper-eligible players from your roster.

    Args:
        league_id: Yahoo league ID

    Returns:
        List of keeper-eligible YahooRosterPlayer objects
    """
    token = _get_access_token()
    try:
        keepers = fetch_yahoo_keepers(token)
        return keepers or []
    except Exception as e:
        logger.error(f"Error fetching keepers: {e}")
        raise


def get_sync_league_teams(league_id: str) -> List[Dict[str, Any]]:
    """
    Get all teams in a league.

    Args:
        league_id: Yahoo league ID

    Returns:
        List of team dictionaries with team_key, team_id, name, manager_name
    """
    token = _get_access_token()
    try:
        teams = fetch_league_teams(token, league_id)
        return teams or []
    except Exception as e:
        logger.error(f"Error fetching league teams: {e}")
        raise


def get_sync_all_team_rosters(league_id: str) -> Dict[str, tuple[str, List[YahooRosterPlayer]]]:
    """
    Get rosters for all teams in a league.

    Args:
        league_id: Yahoo league ID

    Returns:
        Dict mapping team_key to (team_name, roster_players)
    """
    token = _get_access_token()
    try:
        rosters = fetch_all_team_rosters(token, league_id)
        return rosters or {}
    except Exception as e:
        logger.error(f"Error fetching all team rosters: {e}")
        raise


def get_sync_standings_for_year(league_id: str, year: int) -> List[Dict[str, Any]]:
    """
    Get standings for a specific year.

    Args:
        league_id: Yahoo league ID (format: "123.l.456" where 123 is year)
        year: Season year to fetch standings for

    Returns:
        List of standings dictionaries
    """
    token = _get_access_token()
    try:
        # League key format: "{year}.l.{league_number}"
        # Extract league number from current league_id and build year-specific key
        parts = league_id.split('.')
        if len(parts) >= 3:
            league_num = parts[2]
            league_key_for_year = f"{year}.l.{league_num}"
        else:
            league_key_for_year = league_id

        standings_data = fetch_standings(token, league_key_for_year)
        if standings_data:
            return standings_data.get('standings', [])
        return []
    except Exception as e:
        logger.error(f"Error fetching standings for year {year}: {e}")
        raise
