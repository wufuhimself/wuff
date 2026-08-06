"""
MCP client wrapper for Yahoo Fantasy Football.

Thin wrapper around wuff's yahoo_client that sources its access token from
the shared token store (data/auth/yahoo_token.json via token_store.py) --
the same store the Flask app and `auth`/`token`/`refresh` CLI commands use.
This keeps a single, auto-refreshing source of truth for the OAuth token
instead of a second copy tracked separately in .env.
"""

import logging
from typing import Any, Dict, List, Optional

from .token_store import get_valid_token
from .yahoo_client import (
    YahooRosterPlayer,
    fetch_yahoo_rankings,
    fetch_yahoo_roster_players,
    fetch_yahoo_keepers,
    fetch_standings,
    fetch_user_leagues,
    fetch_games,
    fetch_league_teams,
    fetch_all_team_rosters,
)

logger = logging.getLogger(__name__)

def _get_access_token() -> str:
    """Get a valid Yahoo access token, refreshing it via the shared token store if needed."""
    token = get_valid_token()
    if not token:
        raise RuntimeError(
            "No valid Yahoo token found. Run 'python -m app auth-server' "
            "(or 'python -m app auth' + 'python -m app token <code>') to authorize."
        )
    return token.access_token


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
        # Try current year first, then previous year, then year before
        current_year = datetime.datetime.now().year
        years_to_try = [current_year, current_year - 1, current_year - 2]

        games = None
        for year in years_to_try:
            try:
                games = fetch_games(token, [year])
                if games:
                    break
            except Exception:
                continue

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
        logger.error("Error fetching leagues: %s", e)
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
        logger.error("Error fetching standings: %s", e)
        raise


def get_sync_roster(_league_id: str, _team_key: Optional[str] = None) -> List[YahooRosterPlayer]:
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
        logger.error("Error fetching roster: %s", e)
        raise


def get_sync_draft_rankings(_league_id: str) -> List[Dict[str, Any]]:
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
        logger.error("Error fetching draft rankings: %s", e)
        raise


def get_sync_keepers(_league_id: str) -> List[YahooRosterPlayer]:
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
        logger.error("Error fetching keepers: %s", e)
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
        logger.error("Error fetching league teams: %s", e)
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
        logger.error("Error fetching all team rosters: %s", e)
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
        logger.error("Error fetching standings for year %s: %s", year, e)
        raise
