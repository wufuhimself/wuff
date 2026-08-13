"""Sync orchestration for Sleeper leagues into wuff's local data layer.

Sleeper is readonly and unauthenticated, so "sync" just means: fetch the
current state from the API and overwrite the local JSON snapshot. There is
no push side, and no token to manage — see [[feedback_yahoo_fantasy_api_approval]]
for contrast with the Yahoo side.

Player IDs from Sleeper are opaque numeric strings, unrelated to Yahoo's
player IDs or to how rankings_manager normalizes names. Everything written
here resolves Sleeper's player_id into a human-readable name/position/team
via the shared players cache, so downstream consumers never have to look up
IDs by hand.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import sleeper_client
from .paths import (
    SLEEPER_LEAGUES_CONFIG_FILE,
    SLEEPER_PLAYERS_CACHE_FILE,
    ensure_parent_dir,
    sleeper_league_dir,
)


def load_sleeper_leagues_config() -> Dict[str, Any]:
    if not SLEEPER_LEAGUES_CONFIG_FILE.exists():
        return {'sleeperUsername': None, 'sleeperUserId': None, 'leagues': []}
    with open(SLEEPER_LEAGUES_CONFIG_FILE, encoding='utf-8') as f:
        return json.load(f)


def save_sleeper_leagues_config(data: Dict[str, Any]) -> None:
    ensure_parent_dir(SLEEPER_LEAGUES_CONFIG_FILE)
    with open(SLEEPER_LEAGUES_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def discover_leagues(username: str, season: str) -> Dict[str, Any]:
    """Resolve a username to their leagues for a season. Does not overwrite
    display names/format for leagues already present in the saved config —
    those are hand-curated (dynasty vs redraft isn't always inferable, and
    display names are nicer edited by hand)."""
    user = sleeper_client.get_user(username)
    user_id = user['user_id']
    leagues = sleeper_client.get_user_leagues(user_id, season)

    existing = load_sleeper_leagues_config()
    existing_by_id = {entry['leagueId']: entry for entry in existing.get('leagues', [])}

    result_leagues = []
    for league in leagues:
        league_id = league['league_id']
        settings = league.get('settings') or {}
        league_type = settings.get('type', 0)
        inferred_format = {0: 'redraft', 1: 'keeper', 2: 'dynasty'}.get(league_type, 'redraft')
        if league_id in existing_by_id:
            result_leagues.append(existing_by_id[league_id])
        else:
            result_leagues.append({
                'leagueId': league_id,
                'name': league.get('name'),
                'format': inferred_format,
                'season': league.get('season'),
                'totalRosters': league.get('total_rosters'),
                'previousLeagueId': league.get('previous_league_id'),
            })

    config = {'sleeperUsername': username, 'sleeperUserId': user_id, 'leagues': result_leagues}
    save_sleeper_leagues_config(config)
    return config


def load_players_cache() -> Dict[str, Any]:
    if not SLEEPER_PLAYERS_CACHE_FILE.exists():
        return {}
    with open(SLEEPER_PLAYERS_CACHE_FILE, encoding='utf-8') as f:
        return json.load(f).get('players', {})


def refresh_players_cache() -> int:
    """Fetch the full Sleeper player dict and cache it locally. This is a
    ~5MB response — call it occasionally (e.g. weekly), not on every sync."""
    players = sleeper_client.get_all_players()
    ensure_parent_dir(SLEEPER_PLAYERS_CACHE_FILE)
    with open(SLEEPER_PLAYERS_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            'fetchedAt': datetime.now(timezone.utc).isoformat(),
            'players': players,
        }, f)
    return len(players)


def _resolve_player(player_id: str, players_cache: Dict[str, Any]) -> Dict[str, Any]:
    info = players_cache.get(player_id)
    if not info:
        return {'playerId': player_id, 'playerName': f'Unknown ({player_id})', 'position': None, 'team': None}
    first = info.get('first_name') or ''
    last = info.get('last_name') or ''
    name = (info.get('full_name') or f'{first} {last}').strip()
    return {
        'playerId': player_id,
        'playerName': name or f'Unknown ({player_id})',
        'position': info.get('position'),
        'team': info.get('team'),
    }


def _write_json(path: Path, data: Any) -> None:
    ensure_parent_dir(path)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def sync_league(league_id: str, players_cache: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Pull league info, rosters, users, and draft results for one league
    and write them to data/raw/sleeper/{league_id}/. Returns a short summary
    for CLI/log output."""
    if players_cache is None:
        players_cache = load_players_cache()

    league_dir = sleeper_league_dir(league_id)
    league = sleeper_client.get_league(league_id)
    users = sleeper_client.get_league_users(league_id)
    rosters = sleeper_client.get_league_rosters(league_id)

    user_by_id = {u['user_id']: u for u in users}

    resolved_rosters = []
    for roster in rosters:
        owner_id = roster.get('owner_id')
        owner = user_by_id.get(owner_id, {})
        team_name = (owner.get('metadata') or {}).get('team_name') or owner.get('display_name') or f'Team {roster.get("roster_id")}'
        players = [_resolve_player(pid, players_cache) for pid in (roster.get('players') or [])]
        starters = [_resolve_player(pid, players_cache) for pid in (roster.get('starters') or []) if pid != '0']
        resolved_rosters.append({
            'rosterId': roster.get('roster_id'),
            'ownerId': owner_id,
            'teamName': team_name,
            'managerDisplayName': owner.get('display_name'),
            'wins': (roster.get('settings') or {}).get('wins'),
            'losses': (roster.get('settings') or {}).get('losses'),
            'ties': (roster.get('settings') or {}).get('ties'),
            'fpts': (roster.get('settings') or {}).get('fpts'),
            'fptsAgainst': (roster.get('settings') or {}).get('fpts_against'),
            'players': players,
            'starters': starters,
        })

    _write_json(league_dir / 'league.json', {
        'leagueId': league_id,
        'name': league.get('name'),
        'season': league.get('season'),
        'status': league.get('status'),
        'settings': league.get('settings'),
        'scoringSettings': league.get('scoring_settings'),
        'rosterPositions': league.get('roster_positions'),
        'syncedAt': datetime.now(timezone.utc).isoformat(),
    })
    _write_json(league_dir / 'rosters.json', resolved_rosters)

    # Draft results, if a draft exists for this league/season yet.
    drafts = sleeper_client.get_league_drafts(league_id)
    draft_summaries = []
    for draft in drafts:
        draft_id = draft['draft_id']
        if draft.get('status') not in ('complete', 'in_progress'):
            continue
        picks = sleeper_client.get_draft_picks(draft_id)
        resolved_picks = []
        for pick in picks:
            metadata = pick.get('metadata') or {}
            resolved_picks.append({
                'round': pick.get('round'),
                'pick': pick.get('pick_no'),
                'rosterId': pick.get('roster_id'),
                'playerId': pick.get('player_id'),
                'playerName': f"{metadata.get('first_name', '')} {metadata.get('last_name', '')}".strip() or None,
                'position': metadata.get('position'),
                'team': metadata.get('team'),
            })
        _write_json(league_dir / f'draft_{draft_id}.json', {
            'draftId': draft_id,
            'season': draft.get('season'),
            'status': draft.get('status'),
            'type': draft.get('type'),
            'picks': resolved_picks,
        })
        draft_summaries.append({'draftId': draft_id, 'status': draft.get('status'), 'pickCount': len(resolved_picks)})

    transaction_count = sync_transactions(league_id, players_cache)
    matchup_week_count = sync_matchups(league_id, league)

    return {
        'leagueId': league_id,
        'name': league.get('name'),
        'rosterCount': len(resolved_rosters),
        'drafts': draft_summaries,
        'transactions': transaction_count,
        'matchupWeeks': matchup_week_count,
    }


# Sleeper reports transactions by "week", not necessarily the calendar week
# the move happened in -- week 0 is a real, populated bucket for pre-season
# activity. The league object exposes no "current week" field to bound this
# by, and re-fetching every week on every sync is cheap (transactions/{week}
# is one call, same budget as a roster fetch) and simpler than trying to
# infer a cutoff, so this always walks the full range and lets Sleeper's own
# empty-list response mark "hasn't happened yet."
TRANSACTION_WEEKS = range(0, 19)


def sync_transactions(league_id: str, players_cache: Optional[Dict[str, Any]] = None) -> int:
    """Fetch every reported week's transactions and write one file per league.

    Deliberately NOT resolved into team names/canonical player ids here --
    that normalization happens once, in app/repository.py's typed API, the
    same place roster/draft normalization happens. This function's job is
    only to get Sleeper's own shape onto disk. Returns the transaction count.
    """
    if players_cache is None:
        players_cache = load_players_cache()

    league_dir = sleeper_league_dir(league_id)
    transactions = []
    for week in TRANSACTION_WEEKS:
        transactions.extend(sleeper_client.get_league_transactions(league_id, week))

    _write_json(league_dir / 'transactions.json', {
        'leagueId': league_id,
        'syncedAt': datetime.now(timezone.utc).isoformat(),
        'transactions': transactions,
    })
    return len(transactions)


def _read_json(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def load_synced_league(league_id: str) -> Optional[Dict[str, Any]]:
    """League settings/status as of the last sleeper-sync run, or None if never synced."""
    return _read_json(sleeper_league_dir(league_id) / 'league.json')


def load_synced_rosters(league_id: str) -> List[Dict[str, Any]]:
    return _read_json(sleeper_league_dir(league_id) / 'rosters.json') or []


def load_synced_drafts(league_id: str) -> List[Dict[str, Any]]:
    """All draft_*.json files synced for this league, most recently synced first."""
    league_dir = sleeper_league_dir(league_id)
    if not league_dir.exists():
        return []
    drafts = []
    for path in sorted(league_dir.glob('draft_*.json')):
        data = _read_json(path)
        if data:
            drafts.append(data)
    return drafts


def load_synced_transactions(league_id: str) -> List[Dict[str, Any]]:
    payload = _read_json(sleeper_league_dir(league_id) / 'transactions.json')
    return (payload or {}).get('transactions', [])


def sync_matchups(league_id: str, league: Optional[Dict[str, Any]] = None) -> int:
    """Fetch every week's matchups through the league's own reported current
    week and write one file per league.

    Bounded by settings.leg -- Sleeper's own "this is the current/last-played
    week" field, present on both an in-progress league and a completed one
    (checked: a finished 17-week regular season still reports leg=17, not
    something reset to 0). Walking to a fixed 18 like transactions does would
    be wasted calls for most of a season and silently return an unbounded
    number of empty rows for a not-yet-played week -- unlike the transactions
    endpoint, an unplayed week's matchups response is NOT empty, it is real
    rows with every score at 0.0, so "response is empty" can't be the signal
    here the way it is for transactions.

    THIS SEASON ONLY. Sleeper chains a league across seasons via
    previous_league_id under a DIFFERENT league_id -- deliberately not walked
    here; see Phase 5 roadmap notes. A league whose season hasn't started yet
    (no games played) legitimately syncs 0 weeks, not an error.
    """
    league = league if league is not None else sleeper_client.get_league(league_id)
    try:
        current_week = int((league.get('settings') or {}).get('leg') or 0)
    except (TypeError, ValueError):
        current_week = 0

    league_dir = sleeper_league_dir(league_id)
    weeks = {}
    for week in range(1, current_week + 1):
        rows = sleeper_client.get_league_matchups(league_id, week)
        if rows:
            weeks[str(week)] = rows

    _write_json(league_dir / 'matchups.json', {
        'leagueId': league_id,
        'season': league.get('season'),
        'syncedAt': datetime.now(timezone.utc).isoformat(),
        'weeks': weeks,
    })
    return len(weeks)


def load_synced_matchups(league_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """{week_str: [roster rows]} as last synced."""
    payload = _read_json(sleeper_league_dir(league_id) / 'matchups.json')
    return (payload or {}).get('weeks', {})


def sync_all_leagues() -> List[Dict[str, Any]]:
    config = load_sleeper_leagues_config()
    players_cache = load_players_cache()
    if not players_cache:
        refresh_players_cache()
        players_cache = load_players_cache()
    results = []
    for entry in config.get('leagues', []):
        results.append(sync_league(entry['leagueId'], players_cache))
    return results
