"""Sync orchestration for ESPN leagues into wuff's local data layer.

Snapshots are written in the SAME shapes sleeper_manager produces ('league',
'rosters', 'drafts' PlatformSnapshot kinds -- see snapshot_models.py), so
the repository layer and the league-overview template work unchanged
regardless of platform.

Private leagues need the user's espn_s2/SWID cookies at sync time — the
web layer stores them encrypted (see app/models.py EspnCredential) and
passes them in; this module never persists credentials.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import espn_client
from .snapshot_store import read_snapshot, read_snapshots, write_snapshot

PLATFORM = 'espn'


def _team_display_name(team: Dict[str, Any]) -> str:
    name = (team.get('name') or '').strip()
    if name:
        return name
    location = (team.get('location') or '').strip()
    nickname = (team.get('nickname') or '').strip()
    return f'{location} {nickname}'.strip() or f"Team {team.get('id')}"


def _resolve_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    player = (entry.get('playerPoolEntry') or {}).get('player') or entry.get('player') or {}
    return {
        'playerId': str(player.get('id', '')),
        'playerName': player.get('fullName') or f"Unknown ({player.get('id')})",
        'position': espn_client.POSITION_BY_ID.get(player.get('defaultPositionId')),
        'team': espn_client.PRO_TEAM_BY_ID.get(player.get('proTeamId'), None),
    }


def _roster_positions(payload: Dict[str, Any]) -> List[str]:
    counts = ((payload.get('settings') or {}).get('rosterSettings') or {}).get('lineupSlotCounts') or {}
    positions: List[str] = []
    for slot_id_str, count in sorted(counts.items(), key=lambda kv: int(kv[0])):
        label = espn_client.LINEUP_SLOT_BY_ID.get(int(slot_id_str), f'SLOT{slot_id_str}')
        positions.extend([label] * int(count))
    return positions


def sync_league(
    league_id: str,
    season: int,
    espn_s2: Optional[str] = None,
    swid: Optional[str] = None,
) -> Dict[str, Any]:
    """Pull league/teams/rosters/draft for one ESPN league and write
    PlatformSnapshot rows. Returns a short summary."""
    payload = espn_client.fetch_league(league_id, season, espn_s2=espn_s2, swid=swid)

    members = {m.get('id'): m.get('displayName') for m in payload.get('members') or []}

    resolved_rosters = []
    player_lookup: Dict[str, Dict[str, Any]] = {}
    for team in payload.get('teams') or []:
        entries = (team.get('roster') or {}).get('entries') or []
        players = [_resolve_entry(entry) for entry in entries]
        starters = [
            _resolve_entry(entry) for entry in entries
            if entry.get('lineupSlotId') not in espn_client.BENCH_SLOTS
        ]
        for player in players:
            player_lookup[player['playerId']] = player
        record = ((team.get('record') or {}).get('overall') or {})
        owners = team.get('owners') or []
        resolved_rosters.append({
            'rosterId': team.get('id'),
            'ownerId': owners[0] if owners else None,
            'teamName': _team_display_name(team),
            'managerDisplayName': members.get(owners[0]) if owners else None,
            'wins': record.get('wins'),
            'losses': record.get('losses'),
            'ties': record.get('ties'),
            'fpts': record.get('pointsFor'),
            'fptsAgainst': record.get('pointsAgainst'),
            'players': players,
            'starters': starters,
        })

    write_snapshot(PLATFORM, league_id, 'league', {
        'leagueId': league_id,
        'name': ((payload.get('settings') or {}).get('name')) or f'ESPN league {league_id}',
        'season': str(payload.get('seasonId') or season),
        'status': 'in_season' if (payload.get('status') or {}).get('isActive') else 'pre_draft',
        'rosterPositions': _roster_positions(payload),
        'syncedAt': datetime.now(timezone.utc).isoformat(),
    })
    write_snapshot(PLATFORM, league_id, 'rosters', resolved_rosters)

    draft = payload.get('draftDetail') or {}
    draft_summaries = []
    if draft.get('drafted') and draft.get('picks'):
        roster_count = max(len(resolved_rosters), 1)
        resolved_picks = []
        for pick in draft['picks']:
            player = player_lookup.get(str(pick.get('playerId')), {})
            resolved_picks.append({
                'round': pick.get('roundId'),
                'pick': pick.get('overallPickNumber') or (
                    ((pick.get('roundId') or 1) - 1) * roster_count + (pick.get('roundPickNumber') or 0)
                ),
                'rosterId': pick.get('teamId'),
                'playerId': str(pick.get('playerId')),
                'playerName': player.get('playerName'),
                'position': player.get('position'),
                'team': player.get('team'),
            })
        season_str = str(payload.get('seasonId') or season)
        write_snapshot(PLATFORM, league_id, 'drafts', {
            'draftId': season_str,
            'season': season_str,
            'status': 'complete',
            'type': 'snake',
            'picks': resolved_picks,
        }, key=season_str)
        draft_summaries.append({'draftId': season_str, 'status': 'complete', 'pickCount': len(resolved_picks)})

    return {
        'leagueId': league_id,
        'name': ((payload.get('settings') or {}).get('name')),
        'rosterCount': len(resolved_rosters),
        'drafts': draft_summaries,
    }


def load_synced_league(league_id: str) -> Optional[Dict[str, Any]]:
    return read_snapshot(PLATFORM, league_id, 'league')


def load_synced_rosters(league_id: str) -> List[Dict[str, Any]]:
    return read_snapshot(PLATFORM, league_id, 'rosters') or []


def load_synced_drafts(league_id: str) -> List[Dict[str, Any]]:
    return read_snapshots(PLATFORM, league_id, 'drafts')
