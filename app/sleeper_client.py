"""Thin wrapper around Sleeper's public, readonly fantasy API.

No auth of any kind is required — every endpoint below is a plain GET.
See https://docs.sleeper.com for the reference. Keep this module dumb:
raw dicts in, raw dicts out. Normalization into wuff's own shapes lives
in sleeper_manager.py.
"""

import os
from typing import Any, Dict, List

import requests

from .rate_limit import RateLimiter

BASE_URL = 'https://api.sleeper.app/v1'

# Sleeper asks all clients to stay under 1000 calls/min. One global budget
# for this process (web + background sync + CLI share it), set well under
# the ceiling to leave headroom.
MAX_CALLS_PER_MIN = int(os.environ.get('SLEEPER_MAX_CALLS_PER_MIN', '600'))
_rate_limiter = RateLimiter(MAX_CALLS_PER_MIN, per_seconds=60.0)


def _get(path: str) -> Any:
    _rate_limiter.acquire()
    resp = requests.get(f'{BASE_URL}{path}', timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_user(username: str) -> Dict[str, Any]:
    """Resolve a Sleeper username to a user object (includes user_id)."""
    return _get(f'/user/{username}')


def get_user_leagues(user_id: str, season: str, sport: str = 'nfl') -> List[Dict[str, Any]]:
    """All leagues a user belongs to for a given season."""
    return _get(f'/user/{user_id}/leagues/{sport}/{season}')


def get_league(league_id: str) -> Dict[str, Any]:
    """League settings/scoring/roster shape."""
    return _get(f'/league/{league_id}')


def get_league_rosters(league_id: str) -> List[Dict[str, Any]]:
    """Current rosters, keyed by numeric Sleeper player_id."""
    return _get(f'/league/{league_id}/rosters')


def get_league_users(league_id: str) -> List[Dict[str, Any]]:
    """Team/manager display names + avatars for a league."""
    return _get(f'/league/{league_id}/users')


def get_league_matchups(league_id: str, week: int) -> List[Dict[str, Any]]:
    """Matchups for a given week (empty list if the week hasn't happened)."""
    return _get(f'/league/{league_id}/matchups/{week}')


def get_league_drafts(league_id: str) -> List[Dict[str, Any]]:
    """Draft objects for a league (usually one, sometimes more for dynasty rookie drafts)."""
    return _get(f'/league/{league_id}/drafts')


def get_draft_picks(draft_id: str) -> List[Dict[str, Any]]:
    """All picks made in a given draft."""
    return _get(f'/draft/{draft_id}/picks')


def get_all_players(sport: str = 'nfl') -> Dict[str, Any]:
    """Full player_id -> player-info dict (~5MB). Cache this; don't call per-sync."""
    return _get(f'/players/{sport}')


def get_league_winners_bracket(league_id: str) -> List[Dict[str, Any]]:
    return _get(f'/league/{league_id}/winners_bracket')


def get_league_losers_bracket(league_id: str) -> List[Dict[str, Any]]:
    return _get(f'/league/{league_id}/losers_bracket')
