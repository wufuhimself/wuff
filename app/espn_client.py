"""Thin wrapper around ESPN's unofficial fantasy football API.

There is no official ESPN fantasy API. These are the JSON endpoints the
ESPN web app itself uses (the same ones the community `espn-api` library
wraps). Public leagues need no auth; private leagues require the user's
`espn_s2` + `SWID` cookies. Expect breakage between seasons — ship
anything built on this labeled beta.

Keep this module dumb: raw dicts in, raw dicts out. Normalization into
wuff's snapshot shapes lives in espn_manager.py.
"""
from typing import Any, Dict, List, Optional

import requests

BASE_URL = 'https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}'

# defaultPositionId -> position; lineupSlotId uses a different table below.
POSITION_BY_ID = {1: 'QB', 2: 'RB', 3: 'WR', 4: 'TE', 5: 'K', 16: 'DEF'}

LINEUP_SLOT_BY_ID = {
    0: 'QB', 2: 'RB', 3: 'RB/WR', 4: 'WR', 5: 'WR/TE', 6: 'TE', 7: 'OP',
    16: 'DEF', 17: 'K', 20: 'BN', 21: 'IR', 23: 'FLEX',
}
BENCH_SLOTS = {20, 21, 24}  # BN, IR, taxi

PRO_TEAM_BY_ID = {
    0: 'FA', 1: 'ATL', 2: 'BUF', 3: 'CHI', 4: 'CIN', 5: 'CLE', 6: 'DAL',
    7: 'DEN', 8: 'DET', 9: 'GB', 10: 'TEN', 11: 'IND', 12: 'KC', 13: 'LV',
    14: 'LAR', 15: 'MIA', 16: 'MIN', 17: 'NE', 18: 'NO', 19: 'NYG',
    20: 'NYJ', 21: 'PHI', 22: 'ARI', 23: 'PIT', 24: 'LAC', 25: 'SF',
    26: 'SEA', 27: 'TB', 28: 'WSH', 29: 'CAR', 30: 'JAX', 33: 'BAL', 34: 'HOU',
}


def _cookies(espn_s2: Optional[str], swid: Optional[str]) -> Optional[Dict[str, str]]:
    if espn_s2 and swid:
        return {'espn_s2': espn_s2, 'SWID': swid}
    return None


def fetch_league(
    league_id: str,
    season: int,
    views: Optional[List[str]] = None,
    espn_s2: Optional[str] = None,
    swid: Optional[str] = None,
) -> Dict[str, Any]:
    """One league payload with the requested views merged in (ESPN merges
    multiple ?view= params into a single response object)."""
    url = BASE_URL.format(season=season, league_id=league_id)
    params = [('view', view) for view in views or ['mTeam', 'mRoster', 'mSettings', 'mDraftDetail']]
    resp = requests.get(url, params=params, cookies=_cookies(espn_s2, swid), timeout=30)
    if resp.status_code == 401:
        raise PermissionError(
            'ESPN says this league is private. Provide your espn_s2 and SWID cookies '
            '(log in at espn.com, copy them from your browser cookies).'
        )
    if resp.status_code == 404:
        raise LookupError(f'ESPN league {league_id} not found for season {season}.')
    resp.raise_for_status()
    payload = resp.json()
    # Historical seasons return a single-element list instead of an object.
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    return payload
